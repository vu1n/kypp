#!/usr/bin/env python3
"""swarm-memory store — milestone 1, on tursodb (the chosen store), per swarm-memory-mcp-server-spec.

  observe = capture   — append-only event (e.g. a §0 trace excerpt). Raw signal.
  claim   = distill   — a durable, distilled memory candidate. Knowledge is COMMIT-FREE; it may
                        ANCHOR to code (symbol / path / search-recipe) so recall brings a live pointer.
  recall  = load/pull — hybrid retrieval (vector semantic + LIKE keyword) with the spec's governance
                        rules (never rejected; prefer accepted; prefer project; provenance always),
                        then re-resolves each claim's code anchors against the current tree.

WHY tursodb (verified embedded): concurrent writes via MVCC + BEGIN CONCURRENT — many
pillboxes/capsules write at once even single-user (SQLite's single-writer was insufficient); native
vector search (vector32 / vector_distance_cos). Storage is behind a thin seam so Postgres (fallback)
is a swap, not a rewrite. Code grounding lives OUTSIDE the store: a claim's code_refs are durable
anchors (symbol / path / search-recipe), resolved against the agent's CURRENT tree at recall by an
optional CodeResolver — ripgrep by default (no index, no daemon, no setup); an AST/BM25 index
(ast-grep, canopy) drops in behind the same seam only when a repo is large enough to pay for it.

Research refinements: `pitfall` type (failure-mining); shared (`global`/`project`) claims should be
MODEL-AGNOSTIC distilled (the MemCollab cross-model landmine) — that stripping is applied by
distill_session on the distill path; a direct `claim()` caller is trusted to keep content agnostic,
not enforced by the store. source attribution + code refs = provenance/grounding. Embeddings are BYO (inject an `embed` fn —
the model choice is separate); with no embedder, recall degrades to LIKE keyword.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol

import turso  # pyturso — the tursodb engine (concurrent writes + native vectors)

from .repository import (  # driver-free seam: capabilities, pointer edges, doc:// grammar
    POINTER_INSERT, Pointer, StoreCapabilities, edges_for_claim, parse_entry_ref, pointer_params, query_tokens,
)
from .vocab import AUTHORITIES, OLLAMA_DEFAULT_HOST, SCOPES, STATUSES, SURFACES, TYPES  # single-sourced vocabulary (turso-free leaf)

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, project TEXT, scope TEXT NOT NULL,
  content TEXT NOT NULL, source TEXT, event_time TEXT, confidence REAL DEFAULT 0.7,
  metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_claims (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL,
  scope TEXT NOT NULL, project TEXT,
  agent TEXT, user TEXT,                 -- authorship provenance (agent/model + human, stamped on EVERY claim) and the match key for the private agent/user scopes
  status TEXT NOT NULL DEFAULT 'candidate',
  authority TEXT DEFAULT 'agent',        -- who asserted it (agent|verified|human) — the survivor tie-break
  confidence REAL DEFAULT 0.7, source_ids TEXT DEFAULT '[]',
  code_refs TEXT DEFAULT '[]',          -- [{symbol?, path?, query?, repo?, commit?}] durable anchor, re-resolved at recall
  verify TEXT,                           -- deterministic freshness check (shell command, exit 0 = still true) that `kypp verify` runs
  embedding BLOB,                        -- vector32 of subject+content (when an embedder is wired)
  valid_from TEXT, valid_to TEXT, metadata TEXT DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_project_idx ON memory_claims(project);
CREATE INDEX IF NOT EXISTS claims_status_idx ON memory_claims(status);
CREATE TABLE IF NOT EXISTS claim_usages (
  id TEXT PRIMARY KEY, consumer TEXT NOT NULL,   -- consumer = the run/session shown the claim
  claim_id TEXT NOT NULL, project TEXT, scope TEXT,
  surface TEXT NOT NULL,                 -- how it was surfaced: 'recall' | 'briefing' | 'expand'
  query TEXT,                            -- the recall query for context, NULL for briefing/expand
  score REAL,                            -- the consumer's verifiable outcome, attributed post-hoc for credit assignment (NULL until then)
  metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usages_consumer_idx ON claim_usages(consumer);
CREATE INDEX IF NOT EXISTS usages_claim_idx ON claim_usages(claim_id);
-- Code ↔ entry edges. entry_ref is the doc:// pin or a symbol:/path: key. path is a hint, not
-- part of the entry's name. Line numbers are not a column: they drift, and recall re-resolves them.
CREATE TABLE IF NOT EXISTS code_pointers (
  id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL DEFAULT '',
  entry_ref TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  doc_id TEXT NOT NULL DEFAULT '',
  anchor TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  symbol TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS code_pointers_edge
  ON code_pointers(kind, entry_ref, path, symbol, claim_id);
CREATE INDEX IF NOT EXISTS code_pointers_path_idx ON code_pointers(path);
CREATE INDEX IF NOT EXISTS code_pointers_anchor_idx ON code_pointers(anchor);
CREATE INDEX IF NOT EXISTS code_pointers_entry_idx ON code_pointers(entry_ref);
CREATE INDEX IF NOT EXISTS code_pointers_claim_idx ON code_pointers(claim_id);
CREATE INDEX IF NOT EXISTS code_pointers_doc_idx ON code_pointers(project, doc_id, anchor);
"""

# Additive column migrations for dbs created before a column existed. turso supports ALTER TABLE ADD
# COLUMN; re-adding raises a "duplicate column" parse error, which __init__ swallows — so this is the
# idempotent way to evolve the schema without a version table. Keep each ADD COLUMN backward-compatible
# (a DEFAULT, never NOT NULL) so old rows stay valid.
# Why: ADD COLUMN with a default only (never NOT NULL, no version table); hosts on different kypp versions share one store file.
_MIGRATIONS = [
    "ALTER TABLE memory_claims ADD COLUMN authority TEXT DEFAULT 'agent'",
    "ALTER TABLE memory_claims ADD COLUMN verify TEXT",
    "ALTER TABLE memory_claims ADD COLUMN user TEXT",  # authorship provenance (the agent column predates it)
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


class BadHandle(ValueError):
    """A claim handle that can't be resolved to one claim — malformed (not 4-32 hex) or an ambiguous
    prefix. A ValueError subclass so existing `except ValueError` still catches it, but typed so a CLI
    can translate just handle mistakes into a clean message, leaving real data errors (e.g. a corrupt
    row's JSON) to surface loud."""


@dataclass
class Claim:
    id: str
    type: str
    subject: str
    content: str
    scope: str
    status: str
    confidence: float
    source_ids: list[str]
    authority: str = "agent"  # who asserted it (agent|verified|human) — the survivor tie-break
    verify: str | None = None  # a deterministic freshness check (shell command) `kypp verify` runs
    code_refs: list[dict] = field(default_factory=list)
    grounding: list[dict] = field(default_factory=list)  # live pointers, populated by recall
    project: str | None = None
    agent: str | None = None  # authoring agent/model (e.g. 'claude-code') — match key for the `agent` scope
    user: str | None = None   # authoring human (e.g. OS login) — match key for the `user` scope
    updated_at: str = ""  # recency — the arbiter ranks on it; recall orders by it
    low_confidence: bool = False
    stale: bool = False  # advisory (set at grounding): code-anchored, but EVERY anchor unresolved — the code it's about is gone


# --- code grounding: resolve a claim's anchor to a LIVE pointer at recall ----------------------
# A code_ref is a DURABLE anchor, not a frozen coordinate. The multiplayer contract: claims are
# commit-free knowledge; each agent re-resolves against its OWN tree; repo/commit/content_hash are
# advisory provenance, never a gate. Shape:
#   {"symbol": "select_backend",             # durable key (fq leaf) — resolved first
#    "path": "src/sandbox/mod.rs",           # last-known location (fast path; may have moved)
#    "query": "select_backend",              # fallback search recipe when the symbol key misses
#    "repo": "pillbox", "commit": "3bbf1b5", # provenance: grounded-as-of
#    "content_hash": "sha256:…"}             # provenance (drift signal once a resolver exposes it)
class CodeResolver(Protocol):
    """Resolve a code_ref to a live location in the current tree. Best-effort, never raises."""

    available: bool

    def resolve(self, ref: dict) -> dict: ...


# Why: resolvers return path:line + one-line preview, never file bodies; inlined code goes stale and burns context.
class RipgrepResolver:
    """Default resolver: ripgrep over the live repo — no index, no daemon, no setup, adequate for
    the common case. Prefers a definition-shaped line, falls back to any mention, then the stored
    recipe. Returns a pointer ({path,line,preview}) + status, never file content — the agent reads
    the current code itself. An AST/BM25 index (ast-grep, canopy) implements the same protocol for
    large repos, behind this seam."""

    _DEF = r"\b(fn|def|func|fun|class|struct|trait|impl|enum|type|interface|const)\b[^\n]*\b{}\b"

    def __init__(self, root: str = ".", binary: str = "rg"):
        self.root = root
        self.binary = shutil.which(binary)

    @property
    def available(self) -> bool:
        return self.binary is not None

    def resolve(self, ref: dict) -> dict:
        leaf = re.split(r"[.:#/]", ref.get("symbol") or "")[-1]
        path = ref.get("path")
        if leaf and path:
            hit = self._rg(self._DEF.format(re.escape(leaf)), path, False) or self._rg(leaf, path, True)
            if hit:
                return {**ref, "status": "grounded", "location": hit}
        if leaf:  # symbol exists somewhere, just not where it was grounded → moved
            hit = self._rg(self._DEF.format(re.escape(leaf)), None, False)
            if hit:
                return {**ref, "status": "moved", "location": hit}
        if ref.get("query"):
            hit = self._rg(ref["query"], None, True)
            if hit:
                return {**ref, "status": "moved", "location": hit}
        return {**ref, "status": "unresolved", "location": None}

    def _rg(self, pattern: str, path: str | None, fixed: bool) -> dict | None:
        if not self.binary:
            return None
        # -H forces the filename even on a single-file search, so output is always path:line:text.
        cmd = [self.binary, "-H", "-n", "--no-heading", "-m", "1"]
        if fixed:
            cmd.append("-F")
        cmd += ["--", pattern, path or "."]  # -- so a symbol/query starting with '-' isn't a flag
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=self.root)
        except (OSError, subprocess.SubprocessError):
            return None
        lines = (p.stdout or "").splitlines()
        if not lines:
            return None
        parts = lines[0].split(":", 2)
        if len(parts) < 3:
            return None
        loc, line, text = parts
        # rg prepends "./" on a directory (repo-wide) search but not on a single-file search —
        # normalize so a grounded vs a moved hit yield the same repo-relative path shape. removeprefix
        # (not lstrip) so it strips the exact prefix once, never gnaws a leading dot off "./.config".
        return {"path": loc.removeprefix("./"), "line": int(line) if line.isdigit() else None,
                "preview": text.strip()}


class MemoryStore:
    """Claim store the engine talks to. This class is the tursodb adapter (MVCC + vectors + LIKE).

    SQLite FTS5 and libsql adapters subclass it and override `_open`, `_native_ids`, and, when
    they have no vectors, `similar_pairs`. Shared governance SQL stays here so `mark_verified`
    remains `kypp.store.MemoryStore.mark_verified`. `embed` is injected; absent, recall is keyword."""

    driver = "turso"

    def __init__(self, path: str = "swarm-memory.db", embed: Callable[[str], list[float]] | None = None,
                 resolver: CodeResolver | None = None):
        self.embed = embed
        self.resolver = resolver
        self.db = self._open(path)
        self._bootstrap()

    def _open(self, path: str):
        db = turso.connect(path)
        cur = db.cursor()
        # pyturso executes lazily — fetch to force the PRAGMA to actually run, then confirm MVCC
        # is on (BEGIN CONCURRENT errors without it).
        mode = cur.execute("PRAGMA journal_mode = 'mvcc'").fetchone()
        _require(bool(mode) and mode[0] == "mvcc", f"MVCC not enabled (got {mode})")
        return db

    def _extra_ddl(self) -> list[str]:
        return []

    def _exec_ddl(self, cur, stmt: str) -> None:
        result = cur.execute(stmt)
        if result is not None and hasattr(result, "fetchall"):
            result.fetchall()

    def _bootstrap(self) -> None:
        cur = self.db.cursor()
        for stmt in filter(str.strip, SCHEMA.split(";")):
            self._exec_ddl(cur, stmt)
        for stmt in _MIGRATIONS:
            try:
                self._exec_ddl(cur, stmt)  # idempotent: re-add raises "duplicate column"
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    raise
        for stmt in self._extra_ddl():
            self._exec_ddl(cur, stmt)
        self.db.commit()

    @property
    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            semantic=True, fts=False, pointers=True, keyword="like", name=self.driver,
        )

    def _begin_sql(self) -> str:
        return "BEGIN CONCURRENT" if self.driver == "turso" else "BEGIN IMMEDIATE"

    def _write(self, sqls: list[tuple[str, tuple]]):
        """Run writes in one transaction. Turso uses BEGIN CONCURRENT and retries an MVCC conflict."""
        for attempt in range(5):
            cur = self.db.cursor()
            try:
                if self.driver != "turso" and getattr(self.db, "in_transaction", False):
                    self.db.commit()
                cur.execute(self._begin_sql())
                for sql, params in sqls:
                    cur.execute(sql, params)
                cur.execute("COMMIT")
                return
            except Exception as e:
                try:
                    cur.execute("ROLLBACK")
                except Exception:
                    pass
                retry = ("conflict" in str(e).lower() or "busy" in str(e).lower()
                         or "locked" in str(e).lower())
                if self.driver == "turso":
                    # pyturso 0.8.1 maps the MVCC COMMIT conflict to DatabaseError,
                    # not OperationalError. Retry only that exact generic error;
                    # constraints, corrupt files and arbitrary Python errors still fail.
                    retry = ((isinstance(e, turso.OperationalError) and retry)
                             or (type(e) is turso.DatabaseError and str(e).strip().lower() == "write-write conflict"))
                if retry and attempt < 4:
                    continue
                raise

    # --- observe = capture --------------------------------------------------
    def observe(self, actor: str, content: str, scope: str = "project", project: str | None = None,
                source: str | None = None, confidence: float = 0.7,
                metadata: dict | None = None, event_time: str | None = None) -> str:
        _require(scope in SCOPES, f"bad scope {scope!r}")
        oid = _uid()
        self._write([(
            "INSERT INTO observations(id,actor,project,scope,content,source,event_time,confidence,metadata,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (oid, actor, project, scope, content, source, event_time, confidence, json.dumps(metadata or {}), _now()))])
        return oid

    # --- claim = distill (may point to code) --------------------------------
    def claim(self, type: str, subject: str, content: str, scope: str, project: str | None = None,
              agent: str | None = None, user: str | None = None, confidence: float = 0.7,
              source_ids: list[str] | None = None,
              code_refs: list[dict] | None = None, accept: bool = False,
              authority: str = "agent", verify: str | None = None) -> str:
        """Write a claim. Status rule (THE one place it lives — callers don't restate it):
        `accept=True` or a non-agent `authority` (human/verified) lands accepted (a human correction or
        verified fact IS the chosen answer), everything else — agent decisions included — is a candidate
        for the arbiter's corroboration gate. `authority` is the
        survivor tie-break — a human correction outranks any agent claim regardless of confidence.
        `verify` is a deterministic freshness check (shell command, exit 0 = still true) that
        `kypp verify` runs to mark the claim verified/stale — the variance-free scoring channel for
        black/white facts."""
        _require(type in TYPES, f"bad type {type!r}")
        _require(scope in SCOPES, f"bad scope {scope!r}")
        _require(authority in AUTHORITIES, f"bad authority {authority!r}")
        status = "accepted" if (accept or authority != "agent") else "candidate"
        cid, now = _uid(), _now()
        vec = self._vec(f"{subject}\n{content}") if (self.embed and self.capabilities.semantic) else None
        # vector32() must wrap a literal; embed it inline (values are our own floats, not user input).
        emb_sql = f"vector32('{vec}')" if vec else "NULL"
        writes: list[tuple[str, tuple]] = [(
            f"INSERT INTO memory_claims(id,type,subject,content,scope,project,agent,user,status,authority,"
            f"confidence,source_ids,code_refs,verify,embedding,created_at,updated_at)"
            f" VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,{emb_sql},?,?)",
            (cid, type, subject, content, scope, project, agent, user, status, authority, confidence,
             json.dumps(source_ids or []), json.dumps(code_refs or []), verify, now, now))]
        writes.extend(self._claim_side_writes(cid, subject, content, code_refs or [], now))
        self._write(writes)
        return cid

    def _claim_side_writes(self, cid: str, subject: str, content: str, code_refs: list[dict],
                           now: str) -> list[tuple[str, tuple]]:
        return [
            (POINTER_INSERT, pointer_params(edge, now=now, new_id=_uid()))
            for edge in edges_for_claim(cid, subject, content, code_refs)
        ]

    def put(self, type: str, subject: str, content: str, scope: str, **kwargs) -> str:
        """`claim` under the repository name. Observations stay on `observe` (append-only raw signal)."""
        return self.claim(type, subject, content, scope, **kwargs)

    # --- set_status = the arbiter's write (supersede / accept / reject) -----
    # Context: doc://kypp/append-only-history@0002#never-delete — change status; never DELETE a claim or observation.
    def set_status(self, claim_id: str, status: str) -> None:
        """Transition a claim's lifecycle status. NEVER deletes — superseded/rejected rows stay
        (recall already excludes them) so history is preserved. The arbiter's only write."""
        _require(status in STATUSES, f"bad status {status!r}")
        self._write([("UPDATE memory_claims SET status=?, updated_at=? WHERE id=?",
                      (status, _now(), claim_id))])

    # --- verify = the deterministic freshness channel (config/fact memory scores itself) ----
    def claims_with_verifier(self, project: str | None = None, subject: str | None = None) -> list[Claim]:
        """Every claim carrying a `verify` command — REGARDLESS of status, so `kypp verify` is
        bidirectional: a claim it rejected last run revives if its check passes again. Not status-gated
        like recall/live_claims; this is the verifier's own view (it owns these claims' freshness).
        Except a SUPERSEDED human claim: a newer correction replaced it and `mark_verified` never
        revives it, so running its command again would only spend a shell call on a dead answer."""
        where, params = self._base_where(project, live_only=False)  # include rejected → revival is bidirectional
        where.append("verify IS NOT NULL")
        where.append("NOT (authority = 'human' AND status = 'superseded')")
        if subject:
            where.append("subject = ?"); params.append(subject)
        cur = self.db.cursor()
        rows = cur.execute("SELECT * FROM memory_claims WHERE " + " AND ".join(where), params).fetchall()
        return self._hydrate(cur, rows)

    def mark_verified(self, claim_id: str, passed: bool) -> None:
        """Record a deterministic check's verdict: pass → accepted + `verified` authority (so the
        confirmed fact outranks any agent guess) — a `human` claim keeps `human`, since a pass only
        confirms it; fail → rejected (the claim is currently FALSE, drop it from recall — a later
        passing check revives it, authority intact). A SUPERSEDED human claim stays superseded on
        BOTH verdicts: a newer human correction replaced it, and reviving it at equal authority with a
        fresh timestamp (directly, or via fail → rejected → pass) would let the stale answer win the
        next consolidation (`reject` keeps a superseded claim superseded for the same reason). A
        human claim's `updated_at` is never refreshed either: a check confirms it, nobody re-asserted
        it, and since recency breaks ties between equal human claims, a sweep landing between
        `kypp correct`'s insert and its consolidate must not make the old answer look newest. The
        verifier's only write."""
        # Context: doc://kypp/authority-order@0008#authority-dominates — verify raises agent → verified, never lowers human.
        if passed:
            self._write([("UPDATE memory_claims SET "
                          "status=CASE WHEN authority='human' AND status='superseded' THEN status ELSE 'accepted' END, "
                          "authority=CASE WHEN authority='human' THEN 'human' ELSE 'verified' END, "
                          "updated_at=CASE WHEN authority='human' THEN updated_at ELSE ? END "
                          "WHERE id=?", (_now(), claim_id))])
        else:
            self._write([("UPDATE memory_claims SET "
                          "status=CASE WHEN authority='human' AND status='superseded' THEN status ELSE 'rejected' END, "
                          "updated_at=CASE WHEN authority='human' THEN updated_at ELSE ? END "
                          "WHERE id=?", (_now(), claim_id))])

    def reject(self, claim_id: str, reason: str | None = None) -> None:
        """Outcome-driven demote: set a claim to `rejected` so recall/briefing drop it, optionally
        recording WHY in `metadata.rejected_reason` (audit). Distinct from supersede (a stronger claim
        on the SAME subject won) and from `correct` (asserts a new value for a subject) — reject is
        "this specific claim is bad," keyed by handle. Not deletion: the row stays (history preserved),
        and a verifier-carrying claim a later `verify` pass can still revive. A SUPERSEDED claim stays
        superseded (only the reason is recorded): it is already out of recall, and turning it into
        `rejected` would make it revivable by `verify`, letting a stale answer compete again with the
        claim that replaced it."""
        cur = self.db.cursor()
        row = cur.execute("SELECT metadata FROM memory_claims WHERE id = ?", (claim_id,)).fetchone()
        meta = json.loads(row[0]) if row and row[0] else {}
        if reason is not None:
            meta["rejected_reason"] = reason
        # status + reason in ONE write (don't split into set_status()+a metadata update — that could
        # half-apply, leaving a rejected claim with no recorded reason).
        self._write([("UPDATE memory_claims SET "
                      "status=CASE WHEN status='superseded' THEN status ELSE 'rejected' END, "
                      "metadata=?, updated_at=? WHERE id=?",
                      (json.dumps(meta), _now(), claim_id))])

    # --- usage = the run -> claims-consumed link (memory credit-assignment foundation) ---
    def record_usage(self, consumer: str, claims, *, surface: str, project: str | None = None,
                     scope: str | None = None, query: str | None = None) -> int:
        """Record that `consumer` (a run/session id) was SHOWN these claims — the inverse of a claim's
        source_ids. The foundation for outcome-driven memory quality: a later pass attributes the
        consumer's verifiable score back to each row by `consumer`, so a claim whose runs underperform
        can be down-weighted/rejected (the 'this memory isn't good' signal that importance/agreement
        heuristics can't give). `claims` may be Claim objects or ids. `surface` is always validated;
        recording is a no-op on an empty consumer or empty set, so a caller can pass an optional
        session id unconditionally."""
        _require(surface in SURFACES, f"bad surface {surface!r}")
        ids = [c.id if isinstance(c, Claim) else c for c in claims]
        if not consumer or not ids:
            return 0
        now = _now()
        self._write([(
            "INSERT INTO claim_usages(id,consumer,claim_id,project,scope,surface,query,metadata,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (_uid(), consumer, cid, project, scope, surface, query, "{}", now)) for cid in ids])
        return len(ids)

    def usages_for(self, consumer: str) -> list[dict]:
        """The claims a run/session was shown, oldest first — observability ('what memory did this run
        see?') and the join key for credit assignment. LEFT JOIN so a usage row survives even after its
        claim is superseded/removed."""
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT u.claim_id, u.surface, u.query, u.score, u.created_at, c.subject, c.type, c.status"
            " FROM claim_usages u LEFT JOIN memory_claims c ON c.id = u.claim_id"
            " WHERE u.consumer = ? ORDER BY u.created_at", (consumer,)).fetchall()
        return [dict(zip([d[0] for d in cur.description], r)) for r in rows]

    def usage_of(self, claim_id: str) -> list[dict]:
        """The runs/sessions a claim was shown to, oldest first — the basis for attributing outcomes."""
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT consumer, surface, score, created_at FROM claim_usages WHERE claim_id = ?"
            " ORDER BY created_at", (claim_id,)).fetchall()
        return [dict(zip([d[0] for d in cur.description], r)) for r in rows]

    # --- recall = load/pull -------------------------------------------------
    def recall(self, query: str, project: str | None = None, scope: str | None = None,
               types: list[str] | None = None, include_candidates: bool = False,
               limit: int = 10, ground: bool = True) -> list[Claim]:
        """Governed retrieval. query='' is BROWSE mode — no text filter, strongest claims first
        (the briefing view); a non-empty query ranks semantically when an embedder is wired, else
        by LIKE keyword. ground=False skips code-anchor resolution (a ripgrep subprocess per ref) —
        for callers that over-fetch then discard; call .ground() on the survivors. Memory is shared:
        recall is author-blind (the agent/user provenance is surfaced on each claim, not a filter)."""
        where, params = self._base_where(project)
        if not include_candidates:
            where.append("status = 'accepted'")
        if scope:
            where.append("scope = ?"); params.append(scope)
        if types:
            where.append("type IN (%s)" % ",".join("?" * len(types))); params.extend(types)
        if not query.strip():
            # browse mode skips the embedder: distance-to-an-empty-string is noise, not ranking.
            cur, rows = self._browse(where, params, limit)
        else:
            # Pointer hits outrank text: a file path or a doc:// anchor is an exact identity, not a bag of words.
            native = self._native_ids(query, where, params, max(limit * 4, 20))
            pointers = self._pointer_claim_ids(query, where, params, limit)
            ordered: list[str] = []
            seen: set[str] = set()
            for cid in pointers + native:
                if cid not in seen:
                    seen.add(cid)
                    ordered.append(cid)
                if len(ordered) >= max(limit * 4, limit):
                    break
            cur, rows = self._in_id_order(ordered, where, params)
            rows = rows[:limit]
        claims = self._hydrate(cur, rows)
        return self.ground(claims) if ground else claims

    def search(self, query: str, **kwargs) -> list[Claim]:
        return self.recall(query, **kwargs)

    def _browse(self, where: list[str], params: list, limit: int):
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT * FROM memory_claims WHERE " + " AND ".join(where)
            + " ORDER BY (status='accepted') DESC, (scope='global') ASC, confidence DESC, updated_at DESC LIMIT ?",
            params + [limit]).fetchall()
        return cur, rows

    def _native_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        """Backend ranking before pointer boost. Turso: cosine when an embedder is wired, else LIKE."""
        if self.embed and query.strip() and self.capabilities.semantic:
            # Why: a linear scan holds to ~1e4-1e5 claims (eval: .brief/docs/turboquant-deferred.md); next step is in-DB DiskANN.
            return self._vector_ids(query, where, params, limit)
        return self._like_ids(query, where, params, limit)

    def _vector_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        qv = self._vec(query)
        if not qv:
            return self._like_ids(query, where, params, limit)
        # Claims with NO embedding still return — NULL distance sorts last — so attaching an embedder
        # never makes prior claims unrecallable. The CASE is load-bearing: vector_distance_cos(NULL, …) THROWS.
        order = (f"CASE WHEN embedding IS NULL THEN NULL "
                 f"ELSE vector_distance_cos(embedding, vector32('{qv}')) END")
        cur = self.db.cursor()
        rows = cur.execute(
            f"SELECT id FROM memory_claims WHERE " + " AND ".join(where)
            + f" ORDER BY ({order}) IS NULL, ({order}), (status='accepted') DESC,"
            + " (scope='global') ASC, confidence DESC LIMIT ?",
            params + [limit]).fetchall()
        return [r[0] for r in rows]

    def _like_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        terms = query_tokens(query)
        if not terms:
            return []
        like = " OR ".join(["(subject LIKE ? OR content LIKE ?)"] * len(terms))
        lp = [f"%{t}%" for t in terms for _ in range(2)]
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT id FROM memory_claims WHERE " + " AND ".join(where)
            + (f" AND ({like})" if terms else "")
            + " ORDER BY (status='accepted') DESC, (scope='global') ASC, confidence DESC, updated_at DESC LIMIT ?",
            params + lp + [limit]).fetchall()
        return [r[0] for r in rows]

    def _in_id_order(self, ids: list[str], where: list[str], params: list):
        cur = self.db.cursor()
        if not ids:
            cur.execute("SELECT * FROM memory_claims WHERE 0")
            return cur, []
        placeholders = ",".join("?" * len(ids))
        rows = cur.execute(
            "SELECT * FROM memory_claims WHERE " + " AND ".join(where) + f" AND id IN ({placeholders})",
            params + ids).fetchall()
        cols = [d[0] for d in cur.description]
        idx = cols.index("id")
        by_id = {r[idx]: r for r in rows}
        return cur, [by_id[i] for i in ids if i in by_id]

    def _pointer_claim_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        """Claims indexed under a path, anchor, or doc:// ref that the query names."""
        clauses: list[str] = []
        binds: list = []
        for match in re.finditer(
            r"doc://([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)@(latest|current|\d{1,4})#([A-Za-z0-9_-]+)",
            query,
        ):
            clauses.append("(entry_ref = ? OR (project = ? AND doc_id = ? AND anchor = ?))")
            binds.extend([match.group(0), match.group(1), match.group(2), match.group(4)])
        for tok in re.findall(r"[^\s]+", query):
            bare = tok.removeprefix("./")
            if "/" in bare or bare.endswith((".py", ".ts", ".md", ".rs", ".go", ".sql")):
                clauses.append("path = ?")
                binds.append(bare)
            elif re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", bare):
                clauses.append("anchor = ?")
                binds.append(bare)
        if not clauses:
            text = query.strip()
            clauses.append("(path = ? OR anchor = ? OR entry_ref = ? OR claim_id = ?)")
            binds.extend([text, text, text, text])
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT id FROM memory_claims WHERE " + " AND ".join(where)
            + " AND id IN (SELECT claim_id FROM code_pointers WHERE claim_id != '' AND ("
            + " OR ".join(clauses) + ")) ORDER BY id LIMIT ?",
            params + binds + [limit]).fetchall()
        return [r[0] for r in rows]

    def get(self, claim_id: str) -> Claim | None:
        """Dereference a claim id or unique prefix — recall/briefing hand out 8-char HANDLES; this is
        how an agent expands one to the full claim (grounding resolved). Returns regardless of status
        (a handle may point into history), or None when unknown. Raises `BadHandle` (a ValueError) for
        the two un-resolvable shapes — malformed (not 4-32 hex) and ambiguous prefix — so a CLI/tool
        caller can distinguish a caller mistake from a clean miss, and from a real data error."""
        if not re.fullmatch(r"[0-9a-f]{4,32}", claim_id):
            raise BadHandle(f"bad claim handle {claim_id!r} (expected 4-32 hex chars)")
        cur = self.db.cursor()
        rows = cur.execute("SELECT * FROM memory_claims WHERE id LIKE ? LIMIT 2",
                           (claim_id + "%",)).fetchall()
        if len(rows) >= 2:
            raise BadHandle(f"ambiguous claim handle {claim_id!r} (matches multiple — use more chars)")
        claims = self.ground(self._hydrate(cur, rows))
        return claims[0] if claims else None

    def live_claims(self, project: str | None = None, subject: str | None = None) -> list[Claim]:
        """Claims eligible for arbitration — everything not superseded/rejected (candidate-INCLUSIVE,
        unlike recall's accepted view). The arbiter consumes Claim objects, so the query + governance
        predicate live HERE, not re-hand-rolled against the table in arbiter."""
        where, params = self._base_where(project)
        if subject:
            where.append("subject = ?"); params.append(subject)
        cur = self.db.cursor()
        rows = cur.execute("SELECT * FROM memory_claims WHERE " + " AND ".join(where), params).fetchall()
        return self._hydrate(cur, rows)

    def similar_pairs(self, project: str | None = None, *, max_distance: float = 0.15) -> list[tuple[str, str]]:
        """Pairs of live, DIFFERENT-subject claims (same scope+project) within `max_distance` cosine —
        the semantic near-dup candidates the arbiter clusters (the LLM-distiller case: distinct subjects,
        same lesson). Empty when claims aren't embedded; exact-subject dups are handled by grouping, not
        here. Closest first. `max_distance` is embedder-dependent — calibrate it (~0.25 for
        nomic-embed-text; the default is illustrative, not universal)."""
        # Same governance predicate as recall/live_claims, single-sourced via _base_where (the `a`
        # side carries project/scope visibility; the join couples `b`'s project to `a`, so `b` only
        # needs the status filter — _base_where(None, "b")).
        wa, params = self._base_where(project, "a")
        wb, _ = self._base_where(None, "b")
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT a.id, b.id FROM memory_claims a JOIN memory_claims b"
            "  ON a.id < b.id AND a.scope = b.scope AND a.subject <> b.subject"
            "  AND (a.project = b.project OR (a.project IS NULL AND b.project IS NULL))"
            " WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL"
            "  AND " + " AND ".join(wa + wb) +
            "  AND vector_distance_cos(a.embedding, b.embedding) < ?"
            " ORDER BY vector_distance_cos(a.embedding, b.embedding)",
            params + [max_distance]).fetchall()
        return [(r[0], r[1]) for r in rows]

    def find_similar(self, project: str | None = None, *, max_distance: float = 0.15) -> list[tuple[str, str]]:
        return self.similar_pairs(project, max_distance=max_distance)

    # --- code ↔ entry pointers ----------------------------------------------
    # Context: doc://kypp/memory-scope-decay@0011#scope-keys-decay — a claim's key is a feature ref; a file path is a hint, and line numbers are not stored.
    def link_pointer(self, entry_ref: str, path: str, *, symbol: str | None = None,
                     claim_id: str | None = None, kind: str = "context") -> str:
        """Index one edge. Idempotent on (kind, entry ref, path, symbol, claim)."""
        path = (path or "").removeprefix("./")
        symbol = symbol or ""
        claim_id = claim_id or ""
        cur = self.db.cursor()
        existing = cur.execute(
            "SELECT id FROM code_pointers WHERE kind = ? AND entry_ref = ? AND path = ? AND symbol = ? AND claim_id = ?",
            (kind, entry_ref, path, symbol, claim_id)).fetchone()
        if existing:
            return existing[0]
        pid = _uid()
        edge = {"entry_ref": entry_ref, "path": path, "symbol": symbol, "claim_id": claim_id, "kind": kind}
        self._write([(POINTER_INSERT, pointer_params(edge, now=_now(), new_id=pid))])
        return pid

    def entries_for_path(self, path: str) -> list[Pointer]:
        """Code → entries. Exact path match after stripping a leading `./`."""
        return self._pointer_rows("path = ? AND path != ''", [(path or "").removeprefix("./")])

    def paths_for_entry(self, entry_ref: str) -> list[Pointer]:
        """Entries → code. A full doc:// ref matches any revision of that anchor; a bare anchor matches too."""
        project, doc_id, _revision, anchor = parse_entry_ref(entry_ref)
        identity = ("project = ? AND doc_id = ? AND anchor = ?" if project
                    else "anchor != '' AND anchor = ?")
        return self._pointer_rows(
            "entry_ref = ? OR claim_id = ? OR (" + identity + ")",
            [entry_ref, entry_ref] + ([project, doc_id, anchor] if project else [anchor]),
        )

    def _pointer_rows(self, where: str, params: list) -> list[Pointer]:
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT * FROM code_pointers WHERE " + where + " ORDER BY path, entry_ref, symbol",
            params).fetchall()
        cols = [d[0] for d in cur.description]
        out = []
        for row in rows:
            record = dict(zip(cols, row))
            out.append(Pointer(
                id=record["id"], entry_ref=record["entry_ref"], project=record["project"] or "",
                doc_id=record["doc_id"] or "", anchor=record["anchor"] or "",
                revision=record["revision"] or "", path=record["path"] or "",
                symbol=record["symbol"] or "", claim_id=record["claim_id"] or "", kind=record["kind"],
            ))
        return out

    # --- helpers ------------------------------------------------------------
    def _hydrate(self, cur, rows) -> list[Claim]:
        """Rows → Claim objects via the cursor's column names — the one row-hydration path."""
        cols = [d[0] for d in cur.description]
        return [self._claim(dict(zip(cols, r))) for r in rows]

    def ground(self, claims: list[Claim]) -> list[Claim]:
        """Resolve each claim's code anchors to live pointers — the one grounding pass (recall + get
        apply it automatically; recall(ground=False) callers apply it to their survivors). No-op
        without an available resolver (CodeResolver declares `available` — no default)."""
        if self.resolver is not None and self.resolver.available:
            for c in claims:
                if c.code_refs:
                    c.grounding = [self.resolver.resolve(ref) for ref in c.code_refs]
                    # code-ref staleness (advisory, NOT destructive — a moved/renamed symbol re-resolves
                    # and the lesson may hold even if the code is gone): every anchor unresolved ⇒ the
                    # code this claim points at no longer exists, so flag it for the reader to weigh.
                    c.stale = all(g.get("status") == "unresolved" for g in c.grounding)
        return claims

    @staticmethod
    def _base_where(project: str | None, alias: str = "", live_only: bool = True) -> tuple[list[str], list]:
        """The governance predicate shared by recall / live_claims / similar_pairs: never surface
        superseded/rejected (unless live_only=False — the verifier owns dead claims' freshness and must
        see them to revive), and a project sees its own claims + global. `alias` prefixes the columns
        for a self-join (e.g. "a"). Single-sourced so the readers can't drift."""
        p = f"{alias}." if alias else ""
        where = [f"{p}status NOT IN ('rejected','superseded')"] if live_only else []
        params: list = []
        if project:
            where.append(f"({p}project = ? OR {p}scope = 'global')")
            params.append(project)
        return where, params

    def _vec(self, text: str) -> str | None:
        """Encode the BYO embedder's output as a vector32 literal, or None for an empty embedding
        (stored as NULL, not vector32('[]')). The values are interpolated into SQL, so a non-numeric /
        NaN / Inf from a buggy embedder fails LOUD here rather than as a malformed query or injection."""
        vals = self.embed(text)
        if not vals:
            return None
        out = []
        for x in vals:
            f = float(x)  # non-numeric → TypeError (loud), never interpolated as text
            _require(math.isfinite(f), f"embedder returned non-finite component {x!r}")
            out.append(f"{f:.6f}")
        return "[" + ",".join(out) + "]"

    @staticmethod
    def _claim(r: dict) -> Claim:
        conf = r["confidence"]
        return Claim(
            id=r["id"], type=r["type"], subject=r["subject"], content=r["content"], scope=r["scope"],
            status=r["status"], confidence=conf, source_ids=json.loads(r["source_ids"]),
            authority=r.get("authority") or "agent", verify=r.get("verify"),
            code_refs=json.loads(r.get("code_refs") or "[]"), project=r["project"], agent=r["agent"],
            user=r.get("user"), updated_at=r.get("updated_at") or "", low_confidence=(conf or 0) < 0.5)


def _require(cond: bool, msg: str):
    if not cond:
        raise ValueError(msg)


def ollama_embed(model: str, host: str = OLLAMA_DEFAULT_HOST, *, timeout: float = 60):
    """A BYO `embed` over a local ollama server (the store's vector-recall seam) — e.g.
    MemoryStore(db, embed=ollama_embed('nomic-embed-text')). Returns the embedding vector for a text.
    NOTE: all claims in one store must share an embedder — vector_distance_cos errors on a dimension
    mismatch, so changing KYPP_EMBED_MODEL requires re-embedding (mixing dims corrupts vector recall)."""
    import urllib.request

    def embed(text: str) -> list[float]:
        body = json.dumps({"model": model, "prompt": text}).encode()
        req = urllib.request.Request(host + "/api/embeddings", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["embedding"]

    return embed


def store_from_env(embed: Callable[[str], list[float]] | None = None) -> MemoryStore:
    """Build a store from the kypp CLI env — the shared constructor for the MCP server and wire CLI.
    KYPP_MEMORY_DB = db path; KYPP_REPO_ROOT = the RipgrepResolver's repo; KYPP_EMBED_MODEL set →
    semantic vector recall via ollama (KYPP_OLLAMA_HOST overrides), else keyword (LIKE) recall."""
    db = os.environ.get("KYPP_MEMORY_DB", os.path.expanduser("~/.kypp/memory.db"))
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)  # bare filename → dirname '' → makedirs raises
    if embed is None and os.environ.get("KYPP_EMBED_MODEL"):
        host = os.environ.get("KYPP_OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        embed = ollama_embed(os.environ["KYPP_EMBED_MODEL"], host)
    # Imported here: backends subclass MemoryStore, so a module-level import would cycle.
    from .backends import open_store
    return open_store(db, embed=embed, resolver=RipgrepResolver(root=os.environ.get("KYPP_REPO_ROOT", ".")),
                      backend=os.environ.get("KYPP_STORE"))


def project_from_env() -> str:
    """The project binding for the env-driven surfaces (MCP server + Bash CLI). Explicit KYPP_PROJECT
    wins; otherwise derive a stable per-repo key from KYPP_REPO_ROOT (default cwd) via the pillbox
    path-key scheme, so live recall/claim share ONE bucket with `sweep`'s captured claims instead of
    every repo collapsing into 'default' (the mis-filing foot-gun). For the derived key to MATCH
    sweep's, run the server with KYPP_REPO_ROOT = the repo the agent works in (the cwd pillbox keys on).
    `sweep`/`capture` are unaffected — they always derive each session's project from its log path."""
    from ._pillbox import project_for_cwd
    return os.environ.get("KYPP_PROJECT") or project_for_cwd(os.environ.get("KYPP_REPO_ROOT", "."))


def identity_from_env() -> tuple[str | None, str | None]:
    """(user, agent) authorship for the env-driven surfaces — stamped on EVERY claim as provenance (so
    future per-author scoring and 'my memories' filters have history) and used as the match key for the
    private `user`/`agent` scopes at recall. KYPP_USER defaults to the OS login so authorship is
    captured zero-config; KYPP_AGENT (the model/harness label, e.g. 'claude-code') has no default —
    set it to populate the per-agent tier (model-quirk memory)."""
    user = os.environ.get("KYPP_USER") or os.environ.get("USER") or None
    agent = os.environ.get("KYPP_AGENT") or None
    return user, agent


if __name__ == "__main__":
    import glob
    db = "/tmp/swarm-memory-selftest.db"
    for f in glob.glob(db + "*"):  # db + WAL/SHM + the MVCC logical-log sidecar
        try: os.remove(f)
        except OSError: pass
    m = MemoryStore(db)
    o = m.observe("dogfood_agent", "Plain cargo build dropped libkrun → docker fallback, no workspace.",
                  scope="project", project="pillbox", confidence=0.9)
    m.claim("pitfall", "libkrun rebuild",
            "Rebuild with --features libkrun + re-codesign; a plain build silently falls to docker.",
            scope="project", project="pillbox", source_ids=[o],
            code_refs=[{"symbol": "select_backend", "path": "src/sandbox/mod.rs",
                        "query": "select_backend", "repo": "pillbox", "commit": "3bbf1b5"}], accept=True)
    m.claim("fact", "low signal", "a weak candidate", scope="project", project="pillbox", confidence=0.3)
    hits = m.recall("libkrun rebuild feature codesign", project="pillbox")
    assert hits and hits[0].type == "pitfall", [h.type for h in hits]
    assert hits[0].source_ids == [o] and hits[0].code_refs[0]["symbol"] == "select_backend", hits[0]
    assert all(h.status == "accepted" for h in hits)
    # usage provenance: record what a run was shown; read it back both ways (run->claims, claim->runs)
    assert m.record_usage("run-1", hits, surface="recall", project="pillbox", query="libkrun") == len(hits)
    seen = m.usages_for("run-1")
    assert any(u["claim_id"] == hits[0].id and u["subject"] == hits[0].subject
               and u["surface"] == "recall" for u in seen), seen
    who = m.usage_of(hits[0].id)
    assert any(u["consumer"] == "run-1" and u["score"] is None for u in who), who  # score NULL until attributed
    assert m.record_usage("", hits, surface="recall") == 0, "empty consumer is a no-op"
    assert m.record_usage("r", [], surface="recall") == 0, "empty claim set is a no-op"
    # authority: a human correction lands ACCEPTED (not candidate) even without accept=True, and
    # hydrates back; a low-confidence human claim still outranks a high-confidence agent one (arbiter).
    hcid = m.claim("fact", "image tag", "use pillbox-runner:l7", scope="project", project="pillbox",
                   confidence=0.4, authority="human")
    hc = m.get(hcid)
    assert hc.authority == "human" and hc.status == "accepted", (hc.authority, hc.status)
    # handle resolution: valid-but-unknown → None (clean miss); malformed → BadHandle (caller mistake)
    assert m.get("deadbeef") is None, "valid hex, no match → None"
    for bad in ("zzzz", "ab", "x"):  # non-hex / too-short
        try:
            m.get(bad); raise AssertionError(f"expected BadHandle for {bad!r}")
        except BadHandle:
            pass
    # verify column round-trips + is queryable via claims_with_verifier (the freshness channel's view)
    vid = m.claim("fact", "verifiable cfg", "x", scope="project", project="pillbox", verify="true")
    assert m.get(vid).verify == "true", m.get(vid).verify
    assert any(c.id == vid for c in m.claims_with_verifier("pillbox")), "verifier claim queryable"
    # mark_verified: pass lifts agent → verified, but never demotes a human claim; fail → reject keeps authority
    m.mark_verified(vid, True)
    assert (m.get(vid).authority, m.get(vid).status) == ("verified", "accepted"), m.get(vid)
    hv = m.claim("fact", "human verifiable cfg", "y", scope="project", project="pillbox",
                 authority="human", verify="true")
    m.mark_verified(hv, False)
    assert (m.get(hv).authority, m.get(hv).status) == ("human", "rejected"), m.get(hv)
    m.mark_verified(hv, True)
    assert (m.get(hv).authority, m.get(hv).status) == ("human", "accepted"), m.get(hv)
    m.set_status(hv, "superseded")  # a newer human correction replaced it
    assert not any(c.id == hv for c in m.claims_with_verifier("pillbox")), "superseded human skipped by sweeps"
    for passed in (True, False, True):  # neither verdict, nor fail → pass, revives it
        m.mark_verified(hv, passed)
        assert (m.get(hv).authority, m.get(hv).status) == ("human", "superseded"), (passed, m.get(hv))
    # a sweep between `correct`'s insert and its consolidate: the old human claim's check passes,
    # but its timestamp stays put, so the just-entered correction still wins by recency.
    race_old = m.claim("fact", "human cfg race", "old", scope="project", project="pillbox",
                       authority="human", verify="true")
    stamp = m.get(race_old).updated_at
    race_new = m.claim("fact", "human cfg race", "new", scope="project", project="pillbox",
                       authority="human")
    m.mark_verified(race_old, False)
    m.mark_verified(race_old, True)
    assert m.get(race_old).updated_at == stamp, (stamp, m.get(race_old).updated_at)
    from .arbiter import consolidate
    consolidate(m, project="pillbox", subject="human cfg race")
    assert m.get(race_new).status == "accepted" and m.get(race_old).status == "superseded", \
        (m.get(race_old), m.get(race_new))
    # superseded → explicit reject → verify pass: the revived old correction keeps its old
    # timestamp, so the newer human correction still wins the next consolidation.
    from .arbiter import consolidate
    old = m.claim("fact", "human cfg ordering", "old", scope="project", project="pillbox",
                  authority="human", verify="true")
    new = m.claim("fact", "human cfg ordering", "new", scope="project", project="pillbox",
                  authority="human")
    consolidate(m, project="pillbox", subject="human cfg ordering")
    assert m.get(old).status == "superseded" and m.get(new).status == "accepted", (m.get(old), m.get(new))
    m.reject(old, reason="outcome attribution")
    m.mark_verified(old, True)
    consolidate(m, project="pillbox", subject="human cfg ordering")
    assert m.get(new).status == "accepted" and m.get(old).status == "superseded", (m.get(old), m.get(new))
    # reject: outcome-driven demote BY HANDLE → drops out of recall, row preserved, reason in metadata
    rj = m.claim("pitfall", "bad lesson", "a lesson that correlated with failed runs",
                 scope="project", project="pillbox", accept=True)
    assert any(h.id == rj for h in m.recall("bad lesson correlated failed", project="pillbox")), "pre-reject recallable"
    m.reject(rj, reason="correlated with 3 failed runs")
    assert m.get(rj).status == "rejected", m.get(rj).status
    assert not any(h.id == rj for h in m.recall("bad lesson correlated failed", project="pillbox")), "post-reject gone from recall"
    _rjmeta = m.db.cursor().execute("SELECT metadata FROM memory_claims WHERE id=?", (rj,)).fetchone()[0]
    assert "correlated with 3 failed runs" in _rjmeta, _rjmeta
    # concurrent writes: two stores writing the same db at once (the tursodb reason)
    m2 = MemoryStore(db)
    a = m.observe("agent_a", "x", project="pillbox"); b = m2.observe("agent_b", "y", project="pillbox")
    assert a != b
    # code grounding: re-resolve the claim's anchor to a LIVE pointer (ripgrep default, no index).
    # Build a throwaway repo so the assertion doesn't depend on this checkout's current layout.
    repo = "/tmp/swarm-memory-selftest-repo"
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(os.path.join(repo, "src", "sandbox"))
    with open(os.path.join(repo, "src", "sandbox", "mod.rs"), "w") as f:
        f.write("fn select_backend(cfg: &Cfg) -> Backend {\n    Backend::Libkrun\n}\n")
    rg = RipgrepResolver(root=repo)
    grounded = ""
    if rg.available:
        g = MemoryStore(db, resolver=rg).recall("libkrun rebuild feature codesign", project="pillbox")[0].grounding
        assert g and g[0]["status"] == "grounded" and g[0]["location"]["line"] == 1, g
        assert not g[0]["location"]["path"].startswith("./"), g  # path-scoped hit: no ./ prefix
        # a wrong stored path → repo-wide search (rg prepends ./) → 'moved', path normalized to match
        moved = rg.resolve({"symbol": "select_backend", "path": "wrong/place.rs", "query": "select_backend"})
        assert moved["status"] == "moved" and not moved["location"]["path"].startswith("./"), moved
        grounded = f"; grounded → {g[0]['location']['path']}:{g[0]['location']['line']}"
        # code-ref STALENESS: a grounded claim is not stale; a claim whose only anchor resolves to
        # nothing (the code is gone) is flagged stale (advisory — the lesson may still hold).
        gs = MemoryStore(db, resolver=rg)
        assert not gs.recall("libkrun rebuild feature codesign", project="pillbox")[0].stale, "grounded → not stale"
        sc = gs.claim("pitfall", "gone code", "lesson about a deleted symbol", scope="project", project="pillbox",
                      accept=True, code_refs=[{"symbol": "deleted_fn", "path": "nowhere.rs", "query": "deleted_fn"}])
        assert gs.get(sc).stale, "all-unresolved anchor → stale"

    # embedder branch (the LIKE→embedder upgrade path the prod code will hit). A toy 1-D embedder; the
    # claims above were written WITHOUT one (embedding=NULL). Recalling under an embedder must still
    # return them (regression: a prior `embedding IS NOT NULL` filter made NULL-embedding claims vanish).
    def toy_embed(text: str) -> list[float]:
        t = text.lower()
        return [float("libkrun" in t), float("docker" in t), 1.0]  # 3-D, non-zero (cosine needs magnitude)
    me = MemoryStore(db, embed=toy_embed)
    me.claim("fact", "embedded note", "a libkrun note stored with an embedding",
             scope="project", project="pillbox", accept=True)
    vh = me.recall("libkrun", project="pillbox")
    assert any(h.subject == "libkrun rebuild" for h in vh), "NULL-embedding claim must still recall"
    assert any(h.subject == "embedded note" for h in vh), vh
    # buggy embedder fails LOUD, not silently / via injection; empty embedding → NULL (no crash)
    try:
        MemoryStore(db, embed=lambda _t: [float("nan")]).claim("fact", "x", "y", scope="project", project="pillbox")
        raise AssertionError("non-finite embedding should have raised")
    except ValueError:
        pass
    MemoryStore(db, embed=lambda _t: []).claim("fact", "empty emb", "z", scope="project", project="pillbox")

    # project-over-global tiebreak: same query + accepted status + confidence → the PROJECT-scoped claim
    # outranks the GLOBAL one (local truth overrides the general tier). 'zoomzoom' (a unique token)
    # isolates this pair from every other seeded claim so the only ranking signal left is scope.
    m.claim("fact", "tier global", "zoomzoom marker at the global tier",
            scope="global", project=None, confidence=0.7, accept=True)
    m.claim("fact", "tier local", "zoomzoom marker for this project",
            scope="project", project="pillbox", confidence=0.7, accept=True)
    ranked = m.recall("zoomzoom", project="pillbox")
    assert [c.scope for c in ranked] == ["project", "global"], [(c.scope, c.confidence) for c in ranked]

    # --- authorship provenance: agent/user record WHO/WHAT created a claim (not a scope, not a filter).
    # Stamped on the claim, hydrated back, surfaced via the handle round-trip; recall stays author-blind
    # (memory is shared — provenance tells you who wrote it, it doesn't hide anything).
    pa = m.claim("fact", "prov a", "qnote from claude", scope="project", project="pillbox",
                 agent="claude-code", user="vuln", accept=True)
    m.claim("fact", "prov b", "qnote from codex", scope="project", project="pillbox",
            agent="codex", user="alice", accept=True)
    assert (m.get(pa).agent, m.get(pa).user) == ("claude-code", "vuln"), m.get(pa)
    # author-blind: a recall returns both authors' claims (nothing is walled off by who wrote it)
    assert {c.subject for c in m.recall("qnote", project="pillbox")} == {"prov a", "prov b"}

    # store_from_env wires the embedder from KYPP_EMBED_MODEL (semantic recall) when set, else None
    os.environ["KYPP_MEMORY_DB"], os.environ["KYPP_EMBED_MODEL"] = db, "fake-embed"
    assert store_from_env().embed is not None, "KYPP_EMBED_MODEL should wire an embedder"
    os.environ.pop("KYPP_EMBED_MODEL")
    assert store_from_env().embed is None, "no model → keyword recall"
    os.environ.pop("KYPP_MEMORY_DB")

    # project_from_env: explicit KYPP_PROJECT wins; else derive the per-repo key from KYPP_REPO_ROOT.
    os.environ.pop("KYPP_PROJECT", None); os.environ.pop("KYPP_REPO_ROOT", None)
    os.environ["KYPP_PROJECT"] = "explicit-proj"
    assert project_from_env() == "explicit-proj", project_from_env()
    del os.environ["KYPP_PROJECT"]
    os.environ["KYPP_REPO_ROOT"] = "/Users/me/code/foo"
    assert project_from_env() == "-Users-me-code-foo", project_from_env()
    os.environ.pop("KYPP_REPO_ROOT")

    # identity_from_env: KYPP_USER (else OS login) + KYPP_AGENT, both overridable
    os.environ["KYPP_USER"], os.environ["KYPP_AGENT"] = "alice", "claude-code"
    assert identity_from_env() == ("alice", "claude-code"), identity_from_env()
    os.environ.pop("KYPP_USER"); os.environ.pop("KYPP_AGENT")
    os.environ["USER"] = "bob"  # KYPP_USER unset → OS login fallback; KYPP_AGENT unset → None
    assert identity_from_env() == ("bob", None), identity_from_env()

    print(f"OK — tursodb store: recalled [{hits[0].type}] {hits[0].subject} → "
          f"code {hits[0].code_refs[0]['path']}:{hits[0].code_refs[0]['symbol']}; "
          f"governance + provenance + concurrent observe + embedder-upgrade-recall all hold{grounded}")
