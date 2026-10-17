"""Staged durable command boundary, NOT connected to legacy claims or public routes.

One group is an aggregate. Its immutable assertion history, events and receipts are
stored as one snapshot so they cannot half-commit. A global retry index fences
command IDs across groups; a claim ID index prevents reuse across groups. This is
intentionally a small first adapter, not a migration or a search projection.
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


def decode_state(text: str) -> State:
    data = json.loads(text)
    claims = tuple(Claim(**{**c, "pending_check": Token(**c["pending_check"]) if c["pending_check"] else None})
                   for c in data.pop("claims"))
    events = tuple(Event(**{**e, "evidence": tuple(Evidence(**v) for v in e["evidence"]),
                            "check_token": Token(**e["check_token"]) if e["check_token"] else None})
                   for e in data.pop("events"))
    receipts = tuple(decode_receipt(r) for r in data.pop("receipts"))
    return State(**data, claims=claims, events=events, receipts=receipts)


class LocalClaimCommands:
    """Use an existing backend connection/transaction runner, in isolated tables.

    The caller authenticates Actor and group reach and supplies canonical Stamp.
    No route exposes this yet. Reads are a single SQL statement (one snapshot).
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
        backend.db.commit()

    def read(self, group: Group) -> State:
        row = self.backend.db.cursor().execute(
            "SELECT state FROM claim_command_groups WHERE group_key = ?", (group.key,),
        ).fetchone()
        return decode_state(row[0]) if row else State()

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
        before = self.read(group)
        after, receipt = transition(before, actor, command, stamp=stamp, k=k)
        writes = [(GUARD, (key, before.revision)), (SAVE, (key, after.revision, encoded(asdict(after))))]
        prior_ids = {c.id for c in before.claims}
        writes.extend(("INSERT INTO claim_command_ids(claim_id,group_key) VALUES(?,?)", (c.id, key))
                      for c in after.claims if c.id not in prior_ids)
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
