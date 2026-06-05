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

from .vocab import OLLAMA_DEFAULT_HOST, SCOPES, STATUSES, TYPES  # single-sourced vocabulary (turso-free leaf)

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, project TEXT, scope TEXT NOT NULL,
  content TEXT NOT NULL, source TEXT, event_time TEXT, confidence REAL DEFAULT 0.7,
  metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_claims (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL,
  scope TEXT NOT NULL, project TEXT, agent TEXT, status TEXT NOT NULL DEFAULT 'candidate',
  confidence REAL DEFAULT 0.7, source_ids TEXT DEFAULT '[]',
  code_refs TEXT DEFAULT '[]',          -- [{symbol?, path?, query?, repo?, commit?}] durable anchor, re-resolved at recall
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


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
    code_refs: list[dict] = field(default_factory=list)
    grounding: list[dict] = field(default_factory=list)  # live pointers, populated by recall
    project: str | None = None
    agent: str | None = None
    updated_at: str = ""  # recency — the arbiter ranks on it; recall orders by it
    low_confidence: bool = False


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
        return {"path": loc, "line": int(line) if line.isdigit() else None, "preview": text.strip()}


class MemoryStore:
    """tursodb-backed store behind a thin seam. Writes use MVCC + BEGIN CONCURRENT (with retry on the
    write-write conflict tursodb signals) so many capsules write concurrently. `embed` is injected
    (BYO embeddings); absent → recall is LIKE-only."""

    def __init__(self, path: str = "swarm-memory.db", embed: Callable[[str], list[float]] | None = None,
                 resolver: CodeResolver | None = None):
        self.embed = embed
        self.resolver = resolver
        self.db = turso.connect(path)
        cur = self.db.cursor()
        # pyturso executes lazily — fetch to force the PRAGMA to actually run, then confirm MVCC
        # is on (BEGIN CONCURRENT errors without it).
        mode = cur.execute("PRAGMA journal_mode = 'mvcc'").fetchone()
        _require(bool(mode) and mode[0] == "mvcc", f"MVCC not enabled (got {mode})")
        for stmt in filter(str.strip, SCHEMA.split(";")):
            cur.execute(stmt).fetchall()  # force DDL (lazy execute)
        self.db.commit()

    def _write(self, sqls: list[tuple[str, tuple]]):
        """Run writes in a BEGIN CONCURRENT txn, retrying the conflict tursodb raises on overlap."""
        for attempt in range(5):
            cur = self.db.cursor()
            try:
                cur.execute("BEGIN CONCURRENT")
                for sql, params in sqls:
                    cur.execute(sql, params)
                cur.execute("COMMIT")
                return
            except turso.OperationalError as e:
                try:
                    cur.execute("ROLLBACK")
                except Exception:
                    pass
                if "conflict" in str(e).lower() or "busy" in str(e).lower():
                    if attempt < 4:
                        continue  # non-overlapping retry; tursodb's MVCC conflict path
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
              agent: str | None = None, confidence: float = 0.7, source_ids: list[str] | None = None,
              code_refs: list[dict] | None = None, accept: bool = False) -> str:
        """Write a claim. Status rule (THE one place it lives — callers don't restate it):
        `accept=True` or type='decision' lands accepted (a decision IS the chosen answer; making it
        a candidate would hide it from default recall), everything else is a candidate for the
        arbiter."""
        _require(type in TYPES, f"bad type {type!r}")
        _require(scope in SCOPES, f"bad scope {scope!r}")
        status = "accepted" if (accept or type == "decision") else "candidate"
        cid, now = _uid(), _now()
        vec = self._vec(f"{subject}\n{content}") if self.embed else None
        # vector32() must wrap a literal; embed it inline (values are our own floats, not user input).
        emb_sql = f"vector32('{vec}')" if vec else "NULL"
        self._write([(
            f"INSERT INTO memory_claims(id,type,subject,content,scope,project,agent,status,confidence,"
            f"source_ids,code_refs,embedding,created_at,updated_at)"
            f" VALUES(?,?,?,?,?,?,?,?,?,?,?,{emb_sql},?,?)",
            (cid, type, subject, content, scope, project, agent, status, confidence,
             json.dumps(source_ids or []), json.dumps(code_refs or []), now, now))])
        return cid

    # --- set_status = the arbiter's write (supersede / accept / reject) -----
    def set_status(self, claim_id: str, status: str) -> None:
        """Transition a claim's lifecycle status. NEVER deletes — superseded/rejected rows stay
        (recall already excludes them) so history is preserved. The arbiter's only write."""
        _require(status in STATUSES, f"bad status {status!r}")
        self._write([("UPDATE memory_claims SET status=?, updated_at=? WHERE id=?",
                      (status, _now(), claim_id))])

    # --- usage = the run -> claims-consumed link (memory credit-assignment foundation) ---
    def record_usage(self, consumer: str, claims, *, surface: str, project: str | None = None,
                     scope: str | None = None, query: str | None = None) -> int:
        """Record that `consumer` (a run/session id) was SHOWN these claims — the inverse of a claim's
        source_ids. The foundation for outcome-driven memory quality: a later pass attributes the
        consumer's verifiable score back to each row by `consumer`, so a claim whose runs underperform
        can be down-weighted/rejected (the 'this memory isn't good' signal that importance/agreement
        heuristics can't give). `claims` may be Claim objects or ids. No-op on an empty consumer or
        empty set, so a caller can pass an optional session id unconditionally."""
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
        for callers that over-fetch then discard; call .ground() on the survivors."""
        where, params = self._base_where(project)
        if not include_candidates:
            where.append("status = 'accepted'")
        if scope:
            where.append("scope = ?"); params.append(scope)
        if types:
            where.append("type IN (%s)" % ",".join("?" * len(types))); params.extend(types)
        cur = self.db.cursor()
        # browse mode skips the embedder: distance-to-an-empty-string is noise, not ranking.
        qv = self._vec(query) if (self.embed and query.strip()) else None
        if qv:
            # semantic recall: cosine distance to the query embedding (linear scan — fine at bootstrap).
            # Claims with NO embedding (written before an embedder was wired) still return — the CASE
            # gives them NULL distance, sorted last — so attaching an embedder never makes prior claims
            # unrecallable. The CASE is load-bearing: vector_distance_cos(NULL, …) THROWS, not NULL.
            order = (f"CASE WHEN embedding IS NULL THEN NULL "
                     f"ELSE vector_distance_cos(embedding, vector32('{qv}')) END")
            rows = cur.execute(
                f"SELECT *, ({order}) AS _d FROM memory_claims WHERE " + " AND ".join(where)
                + " ORDER BY _d IS NULL, _d, (status='accepted') DESC, confidence DESC LIMIT ?",
                params + [limit]).fetchall()
        else:
            # keyword recall (LIKE) — no embedder wired (or the query produced no embedding).
            terms = re.findall(r"[A-Za-z0-9_]+", query)
            like = " OR ".join(["(subject LIKE ? OR content LIKE ?)"] * len(terms))
            lp = [f"%{t}%" for t in terms for _ in range(2)]  # one pair per term: subject, content
            rows = cur.execute(
                "SELECT * FROM memory_claims WHERE " + " AND ".join(where)
                + (f" AND ({like})" if terms else "")
                + " ORDER BY (status='accepted') DESC, confidence DESC, updated_at DESC LIMIT ?",
                params + lp + [limit]).fetchall()
        claims = self._hydrate(cur, rows)
        return self.ground(claims) if ground else claims

    def get(self, claim_id: str) -> Claim | None:
        """Dereference a claim id or unique prefix — recall/briefing hand out 8-char HANDLES; this is
        how an agent expands one to the full claim (grounding resolved). Returns regardless of status
        (a handle may point into history). None when unknown; an ambiguous prefix raises rather than
        silently picking one."""
        _require(bool(re.fullmatch(r"[0-9a-f]{4,32}", claim_id)), f"bad claim handle {claim_id!r}")
        cur = self.db.cursor()
        rows = cur.execute("SELECT * FROM memory_claims WHERE id LIKE ? LIMIT 2",
                           (claim_id + "%",)).fetchall()
        _require(len(rows) < 2, f"ambiguous claim handle {claim_id!r}")
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
        return claims

    @staticmethod
    def _base_where(project: str | None, alias: str = "") -> tuple[list[str], list]:
        """The governance predicate shared by recall / live_claims / similar_pairs: never surface
        superseded/rejected, and a project sees its own claims + global. `alias` prefixes the columns
        for a self-join (e.g. "a"). Single-sourced so the readers can't drift."""
        p = f"{alias}." if alias else ""
        where = [f"{p}status NOT IN ('rejected','superseded')"]
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
            code_refs=json.loads(r.get("code_refs") or "[]"), project=r["project"], agent=r["agent"],
            updated_at=r.get("updated_at") or "", low_confidence=(conf or 0) < 0.5)


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
    return MemoryStore(db, embed=embed, resolver=RipgrepResolver(root=os.environ.get("KYPP_REPO_ROOT", ".")))


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
        grounded = f"; grounded → {g[0]['location']['path']}:{g[0]['location']['line']}"

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

    # store_from_env wires the embedder from KYPP_EMBED_MODEL (semantic recall) when set, else None
    os.environ["KYPP_MEMORY_DB"], os.environ["KYPP_EMBED_MODEL"] = db, "fake-embed"
    assert store_from_env().embed is not None, "KYPP_EMBED_MODEL should wire an embedder"
    os.environ.pop("KYPP_EMBED_MODEL")
    assert store_from_env().embed is None, "no model → keyword recall"
    os.environ.pop("KYPP_MEMORY_DB")

    print(f"OK — tursodb store: recalled [{hits[0].type}] {hits[0].subject} → "
          f"code {hits[0].code_refs[0]['path']}:{hits[0].code_refs[0]['symbol']}; "
          f"governance + provenance + concurrent observe + embedder-upgrade-recall all hold{grounded}")
