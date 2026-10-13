"""Read git trees. Spec bytes for rules come from the base branch, not from HEAD."""

from __future__ import annotations

import hashlib
import os
import stat
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
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", errors="replace").strip() or f"git {' '.join(args)} failed")
    return proc.stdout.decode("utf-8", errors="surrogateescape")


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
        output = _git(repo, ["ls-files", "-z", "-c", "-o", "--exclude-standard"])
    else:
        output = _git(repo, ["ls-tree", "-z", "-r", "--name-only", rev])
    return list(dict.fromkeys(path for path in output.split("\0") if path))


def _read_rev_file(repo: Path, rev: str, path: str) -> str:
    entry = _git(repo, ["--literal-pathspecs", "ls-tree", "-z", rev, "--", path])
    metadata, separator, name = entry.rstrip("\0").partition("\t")
    if not separator or name != path or metadata.split()[:2] not in (
        ["100644", "blob"], ["100755", "blob"]
    ):
        raise GitError(f"selected entry is not a regular file: {path!r} at {rev}")
    try:
        proc = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True)
    except OSError as exc:
        raise GitError(f"cannot read {path!r} at {rev}: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(f"cannot read {path!r} at {rev}: " + proc.stderr.decode("utf-8", errors="replace").strip())
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitError(f"cannot decode {path!r} at {rev} as UTF-8: {exc}") from exc


def _worktree_entry(repo: Path, path: str, read: bool) -> str | None:
    """Selected content uses no-follow traversal; excluded entries supply existence only."""
    descriptors: list[int] = []
    missing_is_deletion = False
    try:
        if not read:
            missing_is_deletion = True
            try:
                (repo / path).lstat()
            except NotADirectoryError:
                return None
            return ""
        parent = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(parent)
        missing_is_deletion = True
        parts = path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise GitError(f"invalid Git path: {path!r}")
        for part in parts[:-1]:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(parent)
        info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        missing_is_deletion = False
        if not stat.S_ISREG(info.st_mode):
            raise GitError(f"selected entry is not a regular file: {path!r}")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise GitError(f"selected entry is not a regular file: {path!r}")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    except FileNotFoundError as exc:
        if missing_is_deletion:
            return None
        raise GitError(f"cannot read worktree entry {path!r}: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise GitError(f"cannot read worktree entry {path!r}: {exc}") from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def load_files(repo: Path, rev: str | None) -> tuple[dict[str, str], list[str]]:
    paths = list_paths(repo, rev)
    snapshot_paths: list[str] = []
    files: dict[str, str] = {}
    for path in paths:
        selected = want_content(path)
        if rev is None:
            text = _worktree_entry(repo, path, read=selected)
            if text is None:
                continue
        elif selected:
            text = _read_rev_file(repo, rev, path)
        snapshot_paths.append(path)
        if selected:
            files[path] = text
    return files, snapshot_paths


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
