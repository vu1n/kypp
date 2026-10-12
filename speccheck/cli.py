"""CLI. Exit 0 after a report, including when the report lists violations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import Cache
from .engine import run_check
from .gitio import GitError, head_identity, load_files, resolve_baseline, rev_parse
from .report import render_markdown


def _under_brief(path: Path) -> bool:
    return ".brief" in path.resolve().parts


def cmd_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cache_dir = Path(args.cache) if args.cache else repo / ".speccheck-cache"
    if _under_brief(cache_dir):
        print("speccheck: refusing to write a cache under .brief/", file=sys.stderr)
        return 1
    for label, dest in (("report", args.out), ("json", args.json)):
        if dest and _under_brief(Path(dest)):
            print(f"speccheck: refusing to write the {label} under .brief/", file=sys.stderr)
            return 1
    try:
        head_commit = rev_parse(repo, args.head) if args.head else rev_parse(repo, "HEAD")
        if args.head:
            head_files, head_paths = load_files(repo, head_commit)
            head_sha = head_commit
        else:
            head_files, head_paths = load_files(repo, None)
            commit_files, _ = load_files(repo, head_commit)
            head_sha = head_identity(head_commit, commit_files, head_files)
        baseline_sha, note = resolve_baseline(repo, args.baseline, head_commit)
        baseline_files, baseline_paths = load_files(repo, baseline_sha)
    except GitError as exc:
        print(f"speccheck: {exc}", file=sys.stderr)
        return 1
    report = run_check(
        baseline_files,
        head_files,
        baseline_sha=baseline_sha,
        head_sha=head_sha,
        baseline_paths=baseline_paths,
        head_paths=head_paths,
        cache=Cache(cache_dir),
        notes=[note] if note else None,
    )
    text = render_markdown(report)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    if args.out:
        Path(args.out).write_text(text)
    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .bench import format_scores, run_benchmark

    splits = None if args.split == "all" else [args.split]
    scores = run_benchmark(splits)
    sys.stdout.write(format_scores(scores))
    return 0 if all(s.ok for s in scores) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="speccheck", description="Advisory spec-consistency checker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="check head code against the baseline spec")
    check.add_argument("--repo", default=".")
    check.add_argument("--baseline", help="spec commit (default: origin/main or main). HEAD is rejected when its spec differs")
    check.add_argument("--head", help="code commit (default: the worktree)")
    check.add_argument("--out", help="write the markdown report here")
    check.add_argument("--json", help="write the JSON report here")
    check.add_argument("--cache", help="cache directory (default: .speccheck-cache)")
    check.set_defaults(func=cmd_check)

    bench = sub.add_parser("bench", help="false-conformance, abstention, and coverage")
    bench.add_argument("--split", choices=("tuning", "holdout", "smoke", "all"), default="all")
    bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 — a crash is a broken check, not a finding
        print(f"speccheck: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
