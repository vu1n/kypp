"""Storage adapters behind `ClaimStore`.

Search SQL lives here, not in the engine. The hosted-shaped adapter is stdlib
sqlite3 FTS5 (`bm25`, no vectors). The local quality adapter is libsql: the same
FTS5 ranking fused with an exact cosine scan. pyturso stays the default for an
existing MVCC file, because those files are not SQLite databases.
"""
from __future__ import annotations

import os
import sqlite3

from .repository import SemanticUnavailable, StoreCapabilities, query_tokens, reciprocal_rank
from .store import MemoryStore

# unicode61 is the FTS5 default the Worker already ships. Named here so a reader
# sees the tokenizer. remove_diacritics is on by default through SQLite 3.45.
FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5("
    "claim_id UNINDEXED, subject, content, tokenize = \"unicode61\")"
)


class _FtsWrites:
    """FTS5 maintenance shared by the sqlite and libsql adapters."""

    def _extra_ddl(self) -> list[str]:
        return [FTS_DDL,
                "INSERT INTO claims_fts(claim_id,subject,content) "
                "SELECT id,subject,content FROM memory_claims WHERE id NOT IN (SELECT claim_id FROM claims_fts)"]

    def _claim_side_writes(self, cid, subject, content, code_refs, now):
        rows = super()._claim_side_writes(cid, subject, content, code_refs, now)
        rows.append((
            "INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)",
            (cid, subject, content),
        ))
        return rows

    def _fts_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        terms = query_tokens(query)
        if not terms:
            return []
        # Tokens contain no FTS syntax. Visibility/eligibility MUST precede the cap.
        match = " OR ".join(f'"{term}"' for term in terms)
        cur = self.db.cursor()
        rows = cur.execute(
            "SELECT c.id FROM claims_fts JOIN memory_claims c ON c.id=claims_fts.claim_id "
            "WHERE claims_fts MATCH ? AND " + " AND ".join(where)
            + " ORDER BY bm25(claims_fts),c.id LIMIT ?",
            [match, *params, limit],
        ).fetchall()
        return [row[0] for row in rows]


class SqliteFtsStore(_FtsWrites, MemoryStore):
    """Hosted-compatible store: CPython sqlite3, WAL, FTS5 bm25. No vector type."""

    driver = "sqlite"

    def _open(self, path: str):
        db = sqlite3.connect(path)
        db.isolation_level = None  # we issue BEGIN ourselves
        db.execute("PRAGMA journal_mode=WAL").fetchall()
        db.execute("PRAGMA busy_timeout=5000").fetchall()
        return db

    @property
    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            semantic=False, fts=True, pointers=True, keyword="fts5", name=self.driver,
        )

    def _native_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        return self._fts_ids(query, where, params, limit)

    def similar_pairs(self, project: str | None = None, *, max_distance: float = 0.15) -> list[tuple[str, str]]:
        raise SemanticUnavailable(
            "sqlite FTS5 backend has no vector index; semantic dedup is unavailable. "
            "Keyword dedup (exact subject) still runs."
        )


class LibsqlStore(_FtsWrites, MemoryStore):
    """Local store: libsql FTS5 bm25 fused with an exact cosine scan.

    DiskANN exists in libsql (`libsql_vector_idx`) and would approximate. At kypp's
    size an exact scan is the higher-recall search, so `find_similar` does not use it.
    """

    driver = "libsql"

    def _open(self, path: str):
        import libsql

        db = libsql.connect(path)
        cur = db.cursor()
        # libsql opens an implicit transaction; clear it before the first explicit BEGIN.
        try:
            db.commit()
        except Exception:
            pass
        cur.execute("PRAGMA journal_mode=WAL").fetchall()
        cur.execute("PRAGMA busy_timeout=5000").fetchall()
        return db

    @property
    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            semantic=True, fts=True, pointers=True, keyword="fts5", name=self.driver,
        )

    def _native_ids(self, query: str, where: list[str], params: list, limit: int) -> list[str]:
        rankings: list[list[str]] = []
        lexical = self._fts_ids(query, where, params, limit)
        if lexical:
            rankings.append(lexical)
        if self.embed and query.strip():
            semantic = self._vector_ids(query, where, params, limit)
            if semantic:
                rankings.append(semantic)
        if not rankings:
            return []
        if len(rankings) == 1:
            return rankings[0]
        return reciprocal_rank(rankings)


def _turso_file(path: str) -> bool:
    """pyturso MVCC files carry a `-log` sidecar. `SELECT 1` can succeed against the
    header and still leave `sqlite_master` unreadable, so the detector reads the catalog."""
    if os.path.exists(path + "-log"):
        return True
    if not os.path.exists(path):
        return False
    try:
        db = sqlite3.connect(path)
        db.execute("select name from sqlite_master").fetchall()
        db.close()
    except sqlite3.DatabaseError:
        return True
    return False


def open_store(path: str, embed=None, resolver=None, backend: str | None = None):
    """Open the store the engine should use.

    `KYPP_STORE=auto` (the default) keeps an existing pyturso file on pyturso and
    creates a new file on libsql, which is the backend that can do both FTS5 and vectors.
    `sqlite` is the hosted-compatible FTS5 adapter and reports `semantic=False`.
    """
    choice = (backend or "auto").strip().lower()
    if choice == "auto":
        choice = "turso" if _turso_file(path) else "libsql"
    if choice in ("sqlite", "sqlite3", "fts", "d1"):
        return SqliteFtsStore(path, embed=embed, resolver=resolver)
    if choice == "libsql":
        return LibsqlStore(path, embed=embed, resolver=resolver)
    if choice == "turso":
        return MemoryStore(path, embed=embed, resolver=resolver)
    raise ValueError(f"unknown KYPP_STORE {backend!r} (expected auto, libsql, sqlite, or turso)")
