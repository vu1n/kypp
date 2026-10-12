"""Read git trees. Spec bytes for rules come from the base branch, not from HEAD."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


READ_PREFIXES = (
    ".brief/",
    "kypp/",
    "worker/src/",
    "worker/migrations/",
    "docs/",
    ".github/workflows/",
)


def _git(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def rev_parse(repo: Path, rev: str) -> str:
    return _git(repo, ["rev-parse", "--verify", rev]).strip()


def try_rev(repo: Path, rev: str) -> str | None:
    try:
        return rev_parse(repo, rev)
    except GitError:
        return None


def want_content(path: str) -> bool:
    return path.startswith(READ_PREFIXES)


def list_paths(repo: Path, rev: str | None) -> list[str]:
    if rev is None:
        tracked = _git(repo, ["ls-files", "-c", "-o", "--exclude-standard"])
        return [line for line in tracked.splitlines() if line]
    return [line for line in _git(repo, ["ls-tree", "-r", "--name-only", rev]).splitlines() if line]


def _read_rev_file(repo: Path, rev: str, path: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True)
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode()
    except UnicodeDecodeError:
        return None


def _read_worktree_file(repo: Path, path: str) -> str | None:
    file = repo / path
    if not file.is_file():
        return None
    try:
        return file.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def load_files(repo: Path, rev: str | None) -> tuple[dict[str, str], list[str]]:
    paths = list_paths(repo, rev)
    files: dict[str, str] = {}
    for path in paths:
        if not want_content(path):
            continue
        text = _read_worktree_file(repo, path) if rev is None else _read_rev_file(repo, rev, path)
        if text is not None:
            files[path] = text
    return files, paths


def spec_fingerprint(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.startswith(".brief/docs/") or path.startswith(".brief/amendments/") or path == ".brief/aliases.yaml":
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(files[path].encode()).digest())
    return digest.hexdigest()


def head_identity(commit: str, commit_files: dict[str, str], worktree_files: dict[str, str]) -> str:
    if spec_fingerprint(commit_files) == spec_fingerprint(worktree_files) and _code_same(commit_files, worktree_files):
        return commit
    digest = hashlib.sha256()
    keys = sorted(set(worktree_files) | set(commit_files))
    for path in keys:
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(worktree_files.get(path, "").encode()).digest())
    return f"{commit}+dirty+{digest.hexdigest()[:12]}"


def _code_same(left: dict[str, str], right: dict[str, str]) -> bool:
    keys = {p for p in set(left) | set(right) if p.startswith(("kypp/", "worker/"))}
    return all(left.get(p) == right.get(p) for p in keys)


def ratified_sha(repo: Path) -> str | None:
    for rev in ("origin/main", "main"):
        found = try_rev(repo, rev)
        if found:
            return found
    return None


def resolve_baseline(repo: Path, requested: str | None, head_commit: str) -> tuple[str, str | None]:
    """Pick the spec commit. A commit is not judged against its own changed rules."""
    ratified = ratified_sha(repo)
    if requested is None:
        if ratified is None:
            raise GitError("no origin/main or main; pass --baseline")
        return ratified, None
    requested_sha = rev_parse(repo, requested)
    if (
        ratified is not None
        and requested_sha == head_commit
        and requested_sha != ratified
        and spec_fingerprint(load_files(repo, requested_sha)[0]) != spec_fingerprint(load_files(repo, ratified)[0])
    ):
        return ratified, (
            "ignored a baseline equal to HEAD because its spec differs from the base branch; "
            "a change is not judged against its own rules"
        )
    return requested_sha, None
