"""Syntax cache keyed by content hash; semantic references keyed by head SHA.

Reference rows stored for the base SHA are never reused, even when every
dependency hash matches. A row built for some other SHA may be reused only
when its dependency hash still matches (dependency-aware invalidation).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _under_brief(path: Path) -> bool:
    return ".brief" in path.resolve().parts


@dataclass
class StoredRefs:
    sha: str
    dep_hash: str
    payload: dict


class Cache:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None and _under_brief(self.directory):
            raise RuntimeError("refusing to place the cache under .brief/")
        self.syntax_hits = 0
        self.syntax_misses = 0
        self.ref_builds = 0
        self.ref_hits = 0
        self.dependency_reuses = 0
        self.rejected_base = 0
        self._syntax: dict[str, Any] = {}
        self._refs: dict[str, StoredRefs] = {}
        if self.directory is not None:
            self._load_disk()

    def stats(self) -> dict[str, int]:
        return {
            "syntax_hits": self.syntax_hits,
            "syntax_misses": self.syntax_misses,
            "ref_builds": self.ref_builds,
            "ref_hits": self.ref_hits,
            "dependency_reuses": self.dependency_reuses,
            "rejected_base": self.rejected_base,
        }

    def syntax(self, content: str, kind: str, builder: Callable[[], Any]) -> Any:
        digest = content_hash(content.encode())
        key = f"{kind}:{digest}"
        if key in self._syntax:
            self.syntax_hits += 1
            return self._syntax[key]
        disk = self._read_syntax(kind, digest)
        if disk is not None:
            self.syntax_hits += 1
            self._syntax[key] = disk
            return disk
        self.syntax_misses += 1
        value = builder()
        self._syntax[key] = value
        self._write_syntax(kind, digest, value)
        return value

    def references(
        self,
        head_sha: str,
        base_sha: str | None,
        dep_hash: str,
        builder: Callable[[], dict],
    ) -> dict:
        """Return the reference index for ``head_sha``.

        A record whose SHA is ``base_sha`` is never returned when the two
        SHAs differ. Dependency-hash reuse is allowed only for some other SHA.
        """
        if base_sha and base_sha != head_sha and base_sha in self._refs:
            self.rejected_base += 1
        own = self._refs.get(head_sha)
        if own is not None and own.sha == head_sha:
            self.ref_hits += 1
            return own.payload
        for sha, rec in self._refs.items():
            if sha == head_sha:
                continue
            if base_sha and sha == base_sha and sha != head_sha:
                continue
            if rec.sha == base_sha and base_sha != head_sha:
                continue
            if rec.dep_hash == dep_hash:
                self.dependency_reuses += 1
                return rec.payload
        self.ref_builds += 1
        payload = builder()
        payload = dict(payload)
        payload["built_for"] = head_sha
        stored = StoredRefs(sha=head_sha, dep_hash=dep_hash, payload=payload)
        self._refs[head_sha] = stored
        self._write_refs(stored)
        return payload

    def _load_disk(self) -> None:
        assert self.directory is not None
        ref_dir = self.directory / "refs"
        if not ref_dir.exists():
            return
        for path in ref_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            sha = data.get("sha")
            if not sha or path.stem != sha:
                continue
            payload = data.get("payload") or {}
            if payload.get("built_for") != sha:
                continue
            self._refs[sha] = StoredRefs(sha=sha, dep_hash=data.get("dep_hash", ""), payload=payload)

    def _write_refs(self, stored: StoredRefs) -> None:
        if self.directory is None:
            return
        path = self.directory / "refs" / f"{stored.sha}.json"
        self._write_json(path, {"sha": stored.sha, "dep_hash": stored.dep_hash, "payload": stored.payload})

    def _read_syntax(self, kind: str, digest: str) -> Any | None:
        if self.directory is None:
            return None
        path = self.directory / "syntax" / kind / digest
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _write_syntax(self, kind: str, digest: str, value: Any) -> None:
        if self.directory is None:
            return
        self._write_json(self.directory / "syntax" / kind / digest, value)

    def _write_json(self, path: Path, value: Any) -> None:
        if _under_brief(path):
            raise RuntimeError(f"refusing to write cache file under .brief: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
