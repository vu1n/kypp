"""Repository surface the engine calls. Adapters own the SQL.

`put` / `get` / `search` / `find_similar` are the retrieval seam. Governance writes
(`claim`, `set_status`, `mark_verified`, …) stay on the same object because the
engine already performs them and they are not a query language. Search SQL is
hand-written in each backend. This module has no database driver.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from .store import Claim

# Same grammar as speccheck.textscan.DOC_REF_RE. Duplicated so the store does not import the checker.
DOC_REF_RE = re.compile(
    r"doc://([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)@(latest|current|\d{1,4})#([A-Za-z0-9_-]+)"
)

POINTER_INSERT = (
    "INSERT OR IGNORE INTO code_pointers"
    "(id,claim_id,entry_ref,project,doc_id,anchor,revision,path,symbol,kind,created_at)"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
)


class SemanticUnavailable(RuntimeError):
    """The caller asked for vector search on a backend that has no vector index."""


@dataclass(frozen=True)
class StoreCapabilities:
    """What this backend can do. The engine reads these; it does not probe SQL."""

    semantic: bool  # exact cosine over stored embeddings
    fts: bool  # SQLite FTS5 bm25
    pointers: bool  # first-class code ↔ entry edges
    keyword: str  # "fts5" or "like"
    name: str


@dataclass(frozen=True)
class Pointer:
    """One edge between a file and a kypp entry. Line numbers are not stored."""

    id: str
    entry_ref: str
    project: str
    doc_id: str
    anchor: str
    revision: str
    path: str
    symbol: str
    claim_id: str
    kind: str  # "context" (code comment → entry) or "code_ref" (claim → file)


class ClaimStore(Protocol):
    """What the engine needs from a store. Not a connection, not an ORM."""

    capabilities: StoreCapabilities
    embed: Callable[[str], list[float]] | None

    def put(self, type: str, subject: str, content: str, scope: str, **kwargs) -> str: ...
    def get(self, claim_id: str) -> Claim | None: ...
    def search(self, query: str, **kwargs) -> list[Claim]: ...
    def find_similar(self, project: str | None = None, *, max_distance: float = 0.15) -> list[tuple[str, str]]: ...
    def link_pointer(self, entry_ref: str, path: str, *, symbol: str | None = None,
                    claim_id: str | None = None, kind: str = "context") -> str: ...
    def entries_for_path(self, path: str) -> list[Pointer]: ...
    def paths_for_entry(self, entry_ref: str) -> list[Pointer]: ...
    def claim(self, type: str, subject: str, content: str, scope: str, **kwargs) -> str: ...
    def recall(self, query: str, **kwargs) -> list[Claim]: ...
    def ground(self, claims: list[Claim]) -> list[Claim]: ...
    def observe(self, actor: str, content: str, **kwargs) -> str: ...
    def live_claims(self, project: str | None = None, subject: str | None = None) -> list[Claim]: ...
    def set_status(self, claim_id: str, status: str) -> None: ...
    def similar_pairs(self, project: str | None = None, *, max_distance: float = 0.15) -> list[tuple[str, str]]: ...
    def record_usage(self, consumer: str, claims, *, surface: str, **kwargs) -> int: ...


def parse_entry_ref(entry_ref: str) -> tuple[str, str, str, str]:
    """Return `(project, doc_id, revision, anchor)` for a doc:// ref or a code key.

    A bare anchor (`authority-dominates`) has empty project and doc id. `symbol:` and
    `path:` keys are code anchors, not Brief docs.
    """
    text = entry_ref.strip()
    match = DOC_REF_RE.fullmatch(text)
    if match:
        return match.group(1), match.group(2), match.group(3), match.group(4)
    if text.startswith("symbol:") or text.startswith("path:"):
        return "", "code", "", text.split(":", 1)[1]
    return "", "", "", text


def iter_doc_refs(text: str):
    for match in DOC_REF_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        yield match.group(0), match.group(1), match.group(2), match.group(3), match.group(4), line


def edges_for_claim(claim_id: str, subject: str, content: str, code_refs: list[dict]) -> list[dict]:
    """Edges a new claim contributes. Doc refs in the text, plus each code anchor.

    A code anchor's file path is stored. The line the resolver will compute later is not.
    """
    edges: list[dict] = []
    paths: list[tuple[str, str]] = []
    for ref in code_refs or []:
        if not isinstance(ref, dict):
            continue
        path = str(ref.get("path") or "").removeprefix("./")
        symbol = str(ref.get("symbol") or "")
        if not path and not symbol:
            continue
        entry = f"symbol:{symbol}" if symbol else f"path:{path}"
        edges.append({
            "entry_ref": entry, "path": path, "symbol": symbol,
            "claim_id": claim_id, "kind": "code_ref",
        })
        if path:
            paths.append((path, symbol))
    for full, _project, _doc, _rev, _anchor, _line in iter_doc_refs(f"{subject}\n{content}"):
        targets = paths or [("", "")]
        for path, symbol in targets:
            edges.append({
                "entry_ref": full, "path": path, "symbol": symbol,
                "claim_id": claim_id, "kind": "context",
            })
    return edges


def pointer_params(edge: dict, *, now: str, new_id: str) -> tuple:
    project, doc_id, revision, anchor = parse_entry_ref(edge["entry_ref"])
    return (
        new_id, edge.get("claim_id") or "", edge["entry_ref"],
        project, doc_id, anchor, revision,
        str(edge.get("path") or "").removeprefix("./"),
        edge.get("symbol") or "", edge["kind"], now,
    )


def index_context_sources(store: ClaimStore, files: dict[str, str]) -> int:
    """Index `doc://` pointers found in source text. One edge per comment, keyed by path.

    The line number is how the scanner found the comment. It is not stored: lines drift,
    and the lookup key is the file plus the entry ref.
    """
    count = 0
    for path, text in files.items():
        for full, *_rest in iter_doc_refs(text):
            store.link_pointer(full, path, kind="context")
            count += 1
    return count


def reciprocal_rank(rankings: list[list[str]], *, k: int = 60) -> list[str]:
    """Fuse ordered id lists. Scores are ranks, so bm25 and cosine are not compared."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
