"""Fail a PR when a live proposal is invisible to Brief's ratification workflow.

Brief v0.5.2 discovers pending amendments from decision edits, not proposal files.
Use its gate as the authority rather than duplicating its document/hash rules.
This check neither judges nor ratifies an amendment; the normal Brief gate and
review workflow still decide whether a recognized proposal may merge.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from brief.gate import check


def orphaned_proposals(repo: Path, base: str | None = None) -> list[str]:
    repo = repo.resolve()
    brief_dir = repo / ".brief"
    recognized = {
        v.anchor_id for v in check(repo, brief_dir, base)
        if v.kind in {"pending-ratification", "amendment-rejected"}
    }
    # Inspect the same far-side snapshot as Brief: HEAD for a PR range, the index
    # before commit. An unstaged deletion must not hide a staged proposal.
    args = (["ls-tree", "-r", "-z", "--name-only", "HEAD"] if base
            else ["ls-files", "-z"])
    tracked = subprocess.check_output(
        ["git", "-C", str(repo), *args, "--", ".brief/amendments"], text=True)
    return sorted(p for p in tracked.split("\0") if p
                  and Path(p).parent == Path(".brief/amendments")
                  and Path(p).suffix == ".md" and Path(p).stem not in recognized)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="PR base ref; omitted checks staged changes")
    args = parser.parse_args()
    orphans = orphaned_proposals(Path.cwd(), args.base)
    for path in orphans:
        print(f"{path}: proposal is invisible to Brief amendment triage. "
              "Include its proposed decision edit in this PR, plus the "
              "amend-proposed SIGNOFF entry. Do not mint a revision or ratify it yourself.")
    if not orphans:
        print("brief amendments: every live proposal is recognized by the ratification workflow")
    return bool(orphans)


if __name__ == "__main__":
    raise SystemExit(main())
