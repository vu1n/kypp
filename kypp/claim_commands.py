"""Staged durable command boundary, NOT connected to legacy claims or public routes.

Group metadata, claim projections, append-only events and durable retry receipts
commit in one transaction. No persisted row contains the group's entire history.
This remains an isolated adapter, not the live-store migration or search projection.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Protocol

from .claim_machine import (
    Actor, Claim, Command, Event, Evidence, Receipt, Refused, Stamp, State, Token,
    fingerprint, require, transition,
)

# Context: doc://kypp/memory-scope-decay@0011#scope-keys-decay — all group changes, events and receipts commit atomically at the expected revision.
SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_command_groups (
  group_key TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_command_ids (
  claim_id TEXT PRIMARY KEY, group_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_command_receipts (
  principal TEXT NOT NULL, command_id TEXT NOT NULL, group_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(principal, command_id)
);
CREATE TABLE IF NOT EXISTS claim_command_guard (
  ok INTEGER CONSTRAINT claim_command_cas CHECK (ok = 1)
);
"""

# INSERTing zero fails the whole transaction. A successful guard inserts NO row.
GUARD = ("INSERT INTO claim_command_guard(ok) SELECT 0 WHERE "
         "COALESCE((SELECT revision FROM claim_command_groups WHERE group_key = ?), 0) != ?")
SAVE = ("INSERT INTO claim_command_groups(group_key,revision,state) VALUES(?,?,?) "
        "ON CONFLICT(group_key) DO UPDATE SET revision=excluded.revision,state=excluded.state")

# Additive upgrade of the earlier, unactivated experimental tables. Keep the
# existing global ID/receipt keys, and convert old snapshots without losing data.
NORMALIZE_DDL = """
ALTER TABLE claim_command_ids ADD COLUMN ordinal INTEGER DEFAULT 0;
ALTER TABLE claim_command_ids ADD COLUMN claim TEXT;
CREATE INDEX IF NOT EXISTS claim_command_ids_group ON claim_command_ids(group_key,ordinal);
CREATE INDEX IF NOT EXISTS claim_command_receipts_group ON claim_command_receipts(group_key);
CREATE TABLE IF NOT EXISTS claim_command_events (
  group_key TEXT NOT NULL, revision INTEGER NOT NULL, event TEXT NOT NULL,
  PRIMARY KEY(group_key,revision)
);
"""
NORMALIZE_DATA = """
INSERT INTO claim_command_ids(claim_id,group_key,ordinal,claim)
SELECT json_extract(c.value,'$.id'),g.group_key,CAST(c.key AS INTEGER),c.value
FROM claim_command_groups g,json_each(g.state,'$.claims') c
WHERE json_type(g.state,'$.claims')='array'
ON CONFLICT(claim_id) DO UPDATE SET
  group_key=CASE WHEN claim_command_ids.group_key=excluded.group_key THEN excluded.group_key ELSE NULL END,
  ordinal=excluded.ordinal,claim=excluded.claim;
INSERT INTO claim_command_events(group_key,revision,event)
SELECT g.group_key,json_extract(e.value,'$.revision'),e.value
FROM claim_command_groups g,json_each(g.state,'$.events') e
WHERE json_type(g.state,'$.claims')='array';
UPDATE claim_command_groups
SET state=json_object('storage_version',2,'head',json_extract(state,'$.head'),'head_revision',json_extract(state,'$.head_revision'))
WHERE json_type(state,'$.claims')='array';
"""


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Group:
    subject: str
    scope: str
    project: str | None = None
    owner: str | None = None

    @property
    def key(self) -> str:
        require(bool(self.subject.strip()) and self.scope in {"project", "group", "global", "user", "room"}, "invalid_group")
        require((self.owner is not None) == (self.scope in {"user", "room"}), "invalid_group")
        require(self.owner is None or bool(self.owner.strip()), "invalid_group")
        # An unfiled observation has no authorized arbitration group yet.
        require(bool(self.project) if self.scope in {"project", "group"} else self.project is None, "invalid_group")
        return encoded([self.subject, self.scope, self.project, self.owner])


class ClaimCommands(Protocol):
    """Trusted in-process domain API, not a wire schema or an authorization layer."""

    def read(self, group: Group) -> State: ...
    def execute(self, group: Group, actor: Actor, command: Command, *,
                stamp: Stamp | None = None, k: int = 2) -> Receipt: ...


def decode_receipt(data: dict) -> Receipt:
    return Receipt(**{**data, "token": Token(**data["token"]) if data["token"] else None})


def decode_claim(data: dict) -> Claim:
    return Claim(**{**data, "pending_check": Token(**data["pending_check"]) if data["pending_check"] else None})


def decode_event(data: dict) -> Event:
    return Event(**{**data, "evidence": tuple(Evidence(**v) for v in data["evidence"]),
                    "check_token": Token(**data["check_token"]) if data["check_token"] else None})


class LocalClaimCommands:
    """Use an existing backend connection/transaction runner, in isolated tables.

    The caller authenticates Actor and group reach and supplies canonical Stamp.
    No route exposes this yet. A revision sandwich gives multi-query reads one
    consistent group version. Mutation planning never loads receipt/event history.
    An MVCC retry MUST recheck GUARD inside the retried transaction.
    """

    def __init__(self, backend):
        self.backend = backend
        # DDL cannot run inside pyturso's BEGIN CONCURRENT; bootstrap is idempotent,
        # like MemoryStore's schema bootstrap, and happens before commands are used.
        cursor = backend.db.cursor()
        for sql in SCHEMA.split(";"):
            if sql.strip():
                backend._exec_ddl(cursor, sql)
        for sql in NORMALIZE_DDL.split(";"):
            if sql.strip():
                try:
                    backend._exec_ddl(cursor, sql)
                except Exception as error:
                    if "duplicate column" not in str(error).lower():
                        raise
        backend.db.commit()
        backend._write([(sql, ()) for sql in NORMALIZE_DATA.split(";") if sql.strip()])

    def read(self, group: Group) -> State:
        """Full historical view for inspection/conformance; not the mutation read path."""
        return self._read(group, history=True)

    def _read(self, group: Group, *, history: bool) -> State:
        key = group.key
        def header():
            return self.backend.db.cursor().execute(
                "SELECT revision,state FROM claim_command_groups WHERE group_key = ?", (key,),
            ).fetchone()
        for _ in range(5):
            before = header()
            data = json.loads(before[1]) if before else {"storage_version": 2, "head": None, "head_revision": 0}
            require(data.get("storage_version") == 2, "storage_upgrade_required")
            rows = self.backend.db.cursor().execute(
                "SELECT claim FROM claim_command_ids WHERE group_key = ? ORDER BY ordinal", (key,),
            ).fetchall()
            require(all(row[0] is not None for row in rows), "storage_inconsistent")
            claims = tuple(decode_claim(json.loads(row[0])) for row in rows)
            events, receipts = (), ()
            if history:
                rows = self.backend.db.cursor().execute(
                    "SELECT event FROM claim_command_events WHERE group_key = ? ORDER BY revision", (key,),
                ).fetchall()
                events = tuple(decode_event(json.loads(row[0])) for row in rows)
                rows = self.backend.db.cursor().execute(
                    "SELECT receipt FROM claim_command_receipts WHERE group_key = ?", (key,),
                ).fetchall()
                receipts = tuple(sorted((decode_receipt(json.loads(row[0])) for row in rows), key=lambda r: r.revision))
            if header() == before:
                return State(revision=before[0] if before else 0, head=data["head"],
                             head_revision=data["head_revision"], claims=claims, events=events, receipts=receipts)
        raise Refused("stale")

    def _replay(self, key: str, actor: Actor, command: Command) -> Receipt | None:
        row = self.backend.db.cursor().execute(
            "SELECT group_key,fingerprint,receipt FROM claim_command_receipts WHERE principal = ? AND command_id = ?",
            (actor.principal, command.id),
        ).fetchone()
        if row is None:
            return None
        require(row[0] == key and row[1] == fingerprint(actor, command), "idempotency_conflict")
        return decode_receipt(json.loads(row[2]))

    def execute(self, group: Group, actor: Actor, command: Command, *,
                stamp: Stamp | None = None, k: int = 2) -> Receipt:
        key = group.key
        if receipt := self._replay(key, actor, command):
            return receipt
        before = self._read(group, history=False)
        after, receipt = transition(before, actor, command, stamp=stamp, k=k)
        metadata = encoded({"storage_version": 2, "head": after.head, "head_revision": after.head_revision})
        writes = [(GUARD, (key, before.revision)), (SAVE, (key, after.revision, metadata))]
        prior = {c.id: c for c in before.claims}
        for claim in after.claims:
            if claim.id not in prior:
                writes.append(("INSERT INTO claim_command_ids(claim_id,group_key,ordinal,claim) VALUES(?,?,?,?)",
                               (claim.id, key, after.revision, encoded(asdict(claim)))))
            elif claim != prior[claim.id]:
                writes.append(("UPDATE claim_command_ids SET claim=? WHERE claim_id=? AND group_key=?",
                               (encoded(asdict(claim)), claim.id, key)))
        writes.append(("INSERT INTO claim_command_events(group_key,revision,event) VALUES(?,?,?)",
                       (key, after.revision, encoded(asdict(after.events[-1])))))
        writes.append(("INSERT INTO claim_command_receipts(principal,command_id,group_key,fingerprint,receipt) VALUES(?,?,?,?,?)",
                       (actor.principal, command.id, key, receipt.fingerprint, encoded(asdict(receipt)))))
        try:
            self.backend._write(writes)
        except Exception as error:
            # A simultaneous exact delivery returns its first receipt, even if CAS failed.
            if replay := self._replay(key, actor, command):
                return replay
            message = str(error)
            if "claim_command_cas" in message:
                raise Refused("stale") from error
            if "UNIQUE constraint failed: claim_command_ids.claim_id" in message:
                raise Refused("id_exists") from error
            raise
        return receipt
