#!/usr/bin/env python3
"""seed.py — bootstrap a repo's swarm memory from its Claude Code / Codex transcript history.

kypp starts EMPTY on a new repo; meanwhile the agent harnesses have already accumulated that repo's
real working history under ~/.claude/projects/<key> and ~/.codex/sessions. `kypp seed <repo>` translates
that history (transcripts.py → §0) and distills it into the store — so a repo is useful on day one
instead of accruing from zero. The store project is the repo's cwd-key (`project_for_cwd`), the SAME
bucket the live MCP server and `sweep` use, so seeded and live claims co-file (no split-brain).

Distillation quality is the whole game: a CAPABLE distiller (KYPP_DISTILL_MODEL=claude/codex/…) turns
the conversation into real decisions/procedures/pitfalls; the heuristic floor yields only failure
pitfalls. Idempotent per transcript via a `.kypp-seeded` marker (the sweep pattern). A session that
touched the frozen eval tasks is SKIPPED — it must never seed memory you then benchmark on (the
seed-or-donate-never-both discipline; the inverse of why sweep excludes nothing).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

from ._pillbox import project_for_cwd
from .distill import Distiller, distiller_from_env
from .store import MemoryStore, store_from_env
from .transcripts import claude_sessions, codex_sessions, is_eval_contaminated, transcript_events
from .wire import capture_events


def seed_repo(store: MemoryStore, repo: str, *, project: str | None = None,
              distiller: Distiller | None = None, with_codex: bool = False,
              include_eval: bool = False, limit: int = 0, redistill: bool = False) -> dict:
    """Seed the store from a repo's transcript history. `project` defaults to the repo's cwd-key.
    Idempotent via `.kypp-seeded` markers (skip `redistill` to ignore them). Codex sessions aren't
    organized by repo, so they're filtered to those whose recorded cwd IS this repo. Returns tallies."""
    repo = os.path.abspath(os.path.expanduser(repo))
    proj = project or project_for_cwd(repo)
    paths = claude_sessions(repo)  # discovery uses Claude's dir encoder, not the kypp project key
    if with_codex:
        paths += codex_sessions()
    captured = skipped = obs = claims = 0
    for path in paths:
        marker = path + ".kypp-seeded"
        if not redistill and os.path.exists(marker):
            skipped += 1
            continue
        try:
            events, meta = transcript_events(path)
        except (ValueError, OSError, KeyError):
            skipped += 1
            continue
        if meta.get("source") == "codex" and os.path.abspath(meta.get("cwd") or "/") != repo:
            continue  # a codex session from a different repo — not ours, don't mark
        if not include_eval and is_eval_contaminated(meta):
            skipped += 1  # NOT marked — a future contamination-filter refinement must reconsider it
            continue
        if not events:
            skipped += 1
            continue
        res = capture_events(events, store, project=proj, task=meta.get("task", ""), distiller=distiller)
        open(marker, "w").close()
        captured += 1
        obs += res["observations"]
        claims += res["claims"]
        if limit and captured >= limit:
            break
    return {"project": proj, "captured": captured, "skipped": skipped,
            "observations": obs, "claims": claims}


def main():
    ap = argparse.ArgumentParser(description="seed a repo's swarm memory from its Claude Code / Codex history")
    ap.add_argument("repo", nargs="?", default=".", help="repo path (default: cwd)")
    ap.add_argument("--project", default=os.environ.get("KYPP_PROJECT"),
                    help="store project (default: the repo's cwd-key — the live MCP/sweep bucket)")
    ap.add_argument("--codex", action="store_true", help="also scan ~/.codex sessions whose cwd is this repo")
    ap.add_argument("--include-eval", action="store_true",
                    help="do NOT skip eval-task-touching sessions (unsafe if you benchmark on them)")
    ap.add_argument("--no-distill", action="store_true", help="record outcome observations only, skip claims")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap captured sessions (0 = all; a capable distiller is ~1 model call/session)")
    ap.add_argument("--redistill", action="store_true", help="re-process already-seeded sessions (ignore markers)")
    args = ap.parse_args()

    distiller = None if args.no_distill else distiller_from_env()
    if not args.no_distill and not os.environ.get("KYPP_DISTILL_MODEL"):
        print("kypp seed: KYPP_DISTILL_MODEL unset → heuristic floor only (failure pitfalls). Set e.g. "
              "KYPP_DISTILL_MODEL=claude for real decision/procedure/fact claims.", file=sys.stderr)
    res = seed_repo(store_from_env(), args.repo, project=args.project, distiller=distiller,
                    with_codex=args.codex, include_eval=args.include_eval, limit=args.limit,
                    redistill=args.redistill)
    print(f"kypp seed: {res['captured']} session(s) → {res['claims']} claims, {res['observations']} "
          f"observations under project {res['project']!r}; skipped {res['skipped']} "
          f"(already-seeded / eval-contaminated / empty)")


if __name__ == "__main__" and len(sys.argv) > 1:
    main()
elif __name__ == "__main__":
    import json
    import shutil
    import tempfile

    from .distill import HeuristicDistiller

    # contamination filter
    assert is_eval_contaminated({"cwd": "/Users/x/code/pillbox/scripts/eval/tasks/pov", "task": "x"})
    assert is_eval_contaminated({"cwd": "/Users/x/code/foo", "task": "# Instructions\nImplement pov.py"})
    assert not is_eval_contaminated({"cwd": "/Users/x/code/foo", "task": "fix the OAuth refresh bug"})

    # end-to-end: a fake ~/.claude root with one real-shaped session under the repo's key.
    repo = "/Users/x/code/foo"
    root = tempfile.mkdtemp()
    os.environ["KYPP_CLAUDE_ROOT"] = root
    key = project_for_cwd(repo)
    os.makedirs(os.path.join(root, key))
    rows = [
        {"type": "user", "sessionId": "s1", "cwd": repo,
         "message": {"role": "user", "content": "the build keeps failing on the libkrun feature"}},
        {"type": "assistant", "sessionId": "s1", "message": {"model": "claude-opus-4-8", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cargo build"}}]}},
        {"type": "user", "sessionId": "s1", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "missing feature"}]}},
        {"type": "assistant", "sessionId": "s1", "message": {"model": "claude-opus-4-8", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "cargo build"}}]}},
        {"type": "user", "sessionId": "s1", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "still missing"}]}},
    ]
    spath = os.path.join(root, key, "s1.jsonl")
    with open(spath, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    db = tempfile.mktemp(suffix=".db")
    store = MemoryStore(db)
    res = seed_repo(store, repo, distiller=HeuristicDistiller())
    assert res["project"] == key, res
    assert res["captured"] == 1, res
    assert res["claims"] >= 1, res  # repeated Bash failure → a pitfall
    assert os.path.exists(spath + ".kypp-seeded"), "seeded → marked"
    assert seed_repo(store, repo, distiller=HeuristicDistiller())["captured"] == 0, "idempotent"

    shutil.rmtree(root, ignore_errors=True)
    for f in glob.glob(db + "*"):
        try: os.remove(f)
        except OSError: pass
    os.environ.pop("KYPP_CLAUDE_ROOT", None)
    print(f"OK — seed: bootstrapped project {key!r} from a Claude transcript "
          f"({res['claims']} claim(s)), idempotent on re-run; eval-contamination filter holds")
