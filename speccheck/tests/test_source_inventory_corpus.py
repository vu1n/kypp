"""Frozen INV-002 inputs: author without source-loader implementation exposure.

This corpus is exercised only after its original inputs and labels are committed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from speccheck.fixtures import clean_tree


@dataclass(frozen=True)
class Case:
    name: str
    mode: str
    path: str
    kind: str
    outcome: str
    preserve_outputs: bool = False


CASES = (
    Case("committed-clean", "commit", "kypp/control.py", "clean", "report"),
    Case("worktree-clean", "worktree", "kypp/control.py", "clean", "report"),
    *(Case(f"{mode}-{label}-reference", mode, path, "reference", "finding")
      for mode in ("commit", "worktree")
      for label, path in (
          ("space", "kypp/release notes.py"),
          ("tab", "kypp/phase\tgate.py"),
          ("newline", "kypp/first\nsecond.py"),
          ("unicode", "kypp/Δοκιμή雪.py"),
          ("quote", 'kypp/said "ready".py'),
      )),
    Case("committed-late-invalid-byte", "commit", "kypp/late.py", "invalid_tail", "failure"),
    Case("worktree-early-invalid-byte", "worktree", "kypp/early.py", "invalid_prefix", "failure"),
    Case("baseline-invalid-byte", "baseline", "kypp/baseline.py", "invalid_tail", "failure"),
    Case("worktree-truncated-utf8", "worktree", "kypp/truncated.py", "truncated", "failure", True),
    Case("worktree-read-denied", "worktree", "kypp/denied.py", "read_denied", "failure"),
    Case("committed-selected-symlink", "commit", "kypp/linked.py", "symlink", "failure"),
    Case("worktree-selected-symlink", "worktree", "kypp/linked.py", "symlink", "failure", True),
    Case("worktree-directory-replacement", "worktree", "kypp/replaced.py", "directory", "failure"),
    Case("worktree-fifo-replacement", "worktree", "kypp/pipe.py", "fifo", "failure"),
    Case("worktree-intentional-deletion", "worktree", "kypp/removed\n雪.py", "delete", "report"),
    Case("ignored-invalid-control", "worktree", "kypp/ignored.py", "ignored", "report"),
    Case("excluded-invalid-control", "commit", "assets/image.bin", "excluded", "report"),
    Case("excluded-symlink-control", "commit", "assets/link.bin", "excluded_symlink", "report"),
    Case("worktree-valid-utf8-boundaries", "worktree", "kypp/boundary.py", "utf8", "finding"),
)

BROKEN_REFERENCE = "doc:" + "//kypp/corpus-absent@v0042#missing-anchor"
REFERENCE_TEXT = f"# Inventory sentinel: {BROKEN_REFERENCE}\n"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def _commit(repo: Path) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "corpus snapshot")
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, path: str, content: bytes) -> Path:
    dest = repo / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_source_inventory_corpus(tmp_path: Path, case: Case) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "corpus@example.invalid")
    _git(repo, "config", "user.name", "Independent corpus")
    for path, content in clean_tree().items():
        _write(repo, path, content.encode("utf-8"))
    if case.kind in {"delete", "directory", "fifo"}:
        _write(repo, case.path, b"# tracked replacement control\n")
    if case.mode == "baseline":
        _write(repo, case.path, REFERENCE_TEXT.encode() + b"\xff\n")
    baseline = _commit(repo)
    target = repo / case.path
    if case.kind in {"clean", "read_denied"}:
        _write(repo, case.path, b"# ordinary selected source\n")
    elif case.kind == "reference":
        _write(repo, case.path, REFERENCE_TEXT.encode())
    elif case.kind == "invalid_tail":
        _write(repo, case.path, REFERENCE_TEXT.encode() + b"# " + b"a" * 8192 + b"\xff\n")
    elif case.kind == "invalid_prefix":
        _write(repo, case.path, b"\xfe\xff" + REFERENCE_TEXT.encode())
    elif case.kind == "truncated":
        _write(repo, case.path, REFERENCE_TEXT.encode() + b"# truncated \xe2\x82")
    elif case.kind in {"symlink", "excluded_symlink"}:
        outside = tmp_path / "external.py"
        outside.write_text(REFERENCE_TEXT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)
    elif case.kind in {"directory", "fifo", "delete"}:
        target.unlink()
        if case.kind == "directory":
            target.mkdir()
        elif case.kind == "fifo":
            os.mkfifo(target)
    elif case.kind == "ignored":
        _write(repo, ".gitignore", b"kypp/ignored.py\n")
        _write(repo, case.path, b"\xff" + REFERENCE_TEXT.encode())
    elif case.kind == "excluded":
        _write(repo, case.path, b"\xff" + REFERENCE_TEXT.encode())
    elif case.kind == "utf8":
        _write(repo, case.path, ("# \u007f\u0080\u07ff\u0800\uffff\U00010000\U0010ffff\n" + REFERENCE_TEXT).encode())
    else:
        raise AssertionError(case.kind)
    head = _commit(repo) if case.mode in {"commit", "baseline"} else None
    output = tmp_path / "result.md"
    json_output = tmp_path / "result.json"
    cache = tmp_path / "cache"
    before = {}
    if case.preserve_outputs:
        for dest in (output, json_output):
            dest.write_bytes(b"previous output must survive\n")
            before[dest] = hashlib.sha256(dest.read_bytes()).hexdigest()
        cache.mkdir()
        (cache / "existing").write_bytes(b"previous cache\n")
    cache_before = {str(p.relative_to(cache)): p.read_bytes()
                    for p in cache.rglob("*") if p.is_file()} if cache.exists() else {}
    args = ["check", "--repo", str(repo), "--baseline", baseline,
            "--out", str(output), "--json", str(json_output), "--cache", str(cache)]
    if head:
        args.extend(["--head", head])
    bootstrap = "from speccheck.cli import main; import sys; sys.exit(main(sys.argv[1:]))"
    if case.kind == "read_denied":
        # Inject the OS read boundary so root-run CI cannot defeat chmod(000).
        bootstrap = (
            "import io, os, sys; original = io.open; target = " + repr(str(target)) + "; "
            "exec('def denied(file, *a, **kw):\\n"
            "    if os.fspath(file) == target: raise PermissionError(13, \\\"corpus read denied\\\", target)\\n"
            "    return original(file, *a, **kw)'); io.open = denied; "
            "from speccheck.cli import main; sys.exit(main(sys.argv[1:]))"
        )
    env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
    result = subprocess.run([sys.executable, "-c", bootstrap, *args], env=env,
                            capture_output=True, text=True, timeout=15)
    if case.outcome == "failure":
        assert result.returncode != 0, (case.name, result.stdout, result.stderr)
        assert "speccheck:" in result.stderr
        assert not result.stdout.strip(), "read failure must not print a successful report"
        for dest in (output, json_output):
            if dest in before:
                assert hashlib.sha256(dest.read_bytes()).hexdigest() == before[dest]
            else:
                assert not dest.exists()
        cache_after = {str(p.relative_to(cache)): p.read_bytes()
                       for p in cache.rglob("*") if p.is_file()} if cache.exists() else {}
        assert cache_after == cache_before
        return
    assert result.returncode == 0, (case.name, result.stdout, result.stderr)
    assert output.is_file() and json_output.is_file()
    report = json.loads(json_output.read_text())
    assert report["advisory"] is True and report["blocks_merge"] is False
    references = [v for v in report["verdicts"] if v["rule_id"] == "doc-refs"]
    assert references, "reference coverage must be reported"
    if case.outcome == "finding":
        assert any(v["status"] == "violates" and v["counterexample"]
                   and case.path in v["counterexample"] for v in references), references
    else:
        assert all(v["status"] == "conforms" for v in references), references
        assert case.path not in "\n".join(v.get("counterexample") or "" for v in report["verdicts"])
