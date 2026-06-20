#!/usr/bin/env python3
"""evaltasks.py — mine real eval-task candidates from agent transcripts.

The dogfood corpus is also a source of REAL tasks on the user's own distribution — not the imported
swebench/humaneval/aider benchmarks. A transcript carries the eval triple: the TASK (the user's ask),
the SOLUTION (the file edits + commands), and — when the session ran a verifiable check — a ready-made
GRADER (re-run that test, expect pass). This mines those into task CANDIDATES for the gate.py /
memory-matrix harness (the third back-end off the shared transcripts → §0 translator).

The honest boundary: this does NOT synthesize a runnable workspace snapshot, and for sessions that
never ran a passing check it does NOT invent a grader — that's judgment work (a model's or a human's).
A candidate is the structured raw material (prompt + files touched + commands + a grader or a grader
TODO), not yet a frozen task. `auto_gradeable` flags the ones a passing test makes promotable now.

DISCIPLINE (seed-or-donate-never-both): a session promoted to an eval task must NOT also seed memory,
or the memory contains its own benchmark answer. Mine excludes eval-contaminated sessions (the imported
benchmarks) by the same filter seed uses; promoting a candidate should mark its session out of `seed`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from ._pillbox import project_for_cwd
from .seed import _claude_sessions, _codex_sessions, is_eval_contaminated
from .transcripts import transcript_events

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# verifiable-check commands — a passing one is a ready-made grader for the task it concluded.
_TEST_RE = re.compile(
    r"\b(pytest|cargo (test|build)|go test|jest|vitest|mocha|make (test|check)|grade\.sh"
    r"|(npm|pnpm|yarn|bun) (run )?(test|lint|typecheck)|(uv run |python -?m? ?)?pytest|tox|rspec)\b")


def mine_session(events: list[dict], meta: dict) -> dict | None:
    """Extract one task candidate from a translated session, or None if it isn't eval material (no
    clear task ask, or no concrete code change to verify)."""
    edits, commands, passing_tests = [], [], []
    for ev in events:
        p = ev.get("payload") or {}
        if p.get("type") != "tool_call":
            continue
        name, inp = p.get("name", ""), (p.get("input") or {})
        if name in _EDIT_TOOLS:
            fp = inp.get("file_path") or inp.get("path")
            if isinstance(fp, str) and fp:
                edits.append(fp)
        elif name == "Bash":
            cmd = (inp.get("command") or "").strip()
            if cmd:
                commands.append(cmd)
                if _TEST_RE.search(cmd) and p.get("status") == "completed":
                    passing_tests.append(cmd)
    prompt = (meta.get("task") or "").strip()
    if not prompt or not edits:
        return None  # pure Q&A / exploration with no concrete change → not a verifiable task
    grader = ({"kind": "command", "cmd": passing_tests[-1], "note": "the session's own passing check"}
              if passing_tests else
              {"kind": "todo", "note": "no passing check ran — synthesize a grader from the diff / add a test"})
    return {
        "session_id": meta.get("session_id"), "source_kind": meta.get("source"),
        "repo": os.path.basename((meta.get("cwd") or "").rstrip("/")), "cwd": meta.get("cwd"),
        "prompt": prompt, "files_touched": sorted(set(edits)), "n_edits": len(edits),
        "commands": commands[:40], "grader": grader, "auto_gradeable": bool(passing_tests),
    }


def mine_repo(repo: str, *, with_codex: bool = False, auto_only: bool = False, limit: int = 0) -> list[dict]:
    """All task candidates from a repo's transcripts (Claude + optional Codex), eval-contaminated
    sessions excluded so we don't mine the imported benchmarks back out."""
    repo = os.path.abspath(os.path.expanduser(repo))
    paths = _claude_sessions(project_for_cwd(repo))
    if with_codex:
        paths += _codex_sessions()
    candidates: list[dict] = []
    for path in paths:
        try:
            events, meta = transcript_events(path)
        except (ValueError, OSError, KeyError):
            continue
        if meta.get("source") == "codex" and os.path.abspath(meta.get("cwd") or "/") != repo:
            continue
        if is_eval_contaminated(meta):
            continue
        cand = mine_session(events, meta)
        if not cand or (auto_only and not cand["auto_gradeable"]):
            continue
        cand["source"] = path
        candidates.append(cand)
        if limit and len(candidates) >= limit:
            break
    return candidates


def main():
    ap = argparse.ArgumentParser(description="mine real eval-task candidates from a repo's transcripts")
    ap.add_argument("repo", nargs="?", default=".", help="repo path (default: cwd)")
    ap.add_argument("--codex", action="store_true", help="also scan ~/.codex sessions whose cwd is this repo")
    ap.add_argument("--auto-only", action="store_true", help="only candidates with a ready-made (passing-test) grader")
    ap.add_argument("--limit", type=int, default=0, help="cap candidates (0 = all)")
    ap.add_argument("--out", default=None, help="write the candidate manifest (JSON) here (default: stdout summary)")
    args = ap.parse_args()

    cands = mine_repo(args.repo, with_codex=args.codex, auto_only=args.auto_only, limit=args.limit)
    auto = sum(c["auto_gradeable"] for c in cands)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(cands, f, indent=2)
        print(f"kypp mine-tasks: {len(cands)} candidate(s) ({auto} auto-gradeable) → {args.out}")
    else:
        print(f"kypp mine-tasks: {len(cands)} candidate(s), {auto} auto-gradeable (passing test → grader):\n")
        for c in cands[:20]:
            g = "✓grader" if c["auto_gradeable"] else "·todo"
            print(f"  [{g}] {c['repo']}: {c['prompt'][:70].splitlines()[0]}  ({c['n_edits']} edits)")


if __name__ == "__main__" and len(sys.argv) > 1:
    main()
elif __name__ == "__main__":
    # mine_session: a task ask + an edit + a passing test → an auto-gradeable candidate.
    sid = "s1"
    events = [
        {"sessionId": sid, "payload": {"type": "user_message", "text": "add a --json flag to the CLI"}},
        {"sessionId": sid, "payload": {"type": "tool_call", "name": "Edit", "status": "completed",
                                       "input": {"file_path": "src/cli.py"}, "output": "ok"}},
        {"sessionId": sid, "payload": {"type": "tool_call", "name": "Bash", "status": "completed",
                                       "input": {"command": "pytest tests/test_cli.py"}, "output": "2 passed"}},
    ]
    meta = {"session_id": sid, "cwd": "/Users/x/code/foo", "task": "add a --json flag to the CLI", "source": "claude"}
    c = mine_session(events, meta)
    assert c and c["auto_gradeable"], c
    assert c["files_touched"] == ["src/cli.py"] and c["repo"] == "foo", c
    assert c["grader"]["kind"] == "command" and "pytest" in c["grader"]["cmd"], c

    # an edit but no passing check → a candidate with a grader TODO.
    c2 = mine_session(events[:2], meta)
    assert c2 and not c2["auto_gradeable"] and c2["grader"]["kind"] == "todo", c2

    # pure Q&A (no edits) → not a candidate.
    qa = [{"sessionId": sid, "payload": {"type": "user_message", "text": "how does recall work?"}},
          {"sessionId": sid, "payload": {"type": "tool_call", "name": "Read", "status": "completed",
                                         "input": {"file_path": "src/store.py"}, "output": "..."}}]
    assert mine_session(qa, {"task": "how does recall work?", "cwd": "/x"}) is None, "Q&A is not a task"

    print("OK — evaltasks: mined an auto-gradeable candidate (passing test → grader), a grader-TODO "
          "candidate, and correctly rejected a no-edit Q&A session")
