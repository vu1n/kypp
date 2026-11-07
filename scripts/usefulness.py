#!/usr/bin/env python3
"""How useful each claim is, against what its slot gets anyway.

    python3 scripts/usefulness.py [--since YYYY-MM-DD] [--min N] [--from rows.json]

A `used` rating means more for a claim shown at position 9 of a recall than for one at the top of
every briefing: the top slots are read and acted on whatever sits there. So each rated showing is
scored against the used rate of its slot (surface, section, rank band) over every claim, and a claim's
`lift` is its used rate minus that expectation. Positive: it helps more than its placement explains.

Read-only. Without --from it runs one SELECT on the production D1 database through wrangler (run it
from a session with tumbler, as `wrangler` already is), from the worker directory. Only a showing the
session rated counts; an unrated one says nothing either way, and the share rated is printed.
Showings recorded before rows kept their slot (`metadata.position`) fall in an "unplaced" band.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent / "worker"
VERDICTS = {1: "used", 0: "not_used", -1: "wrong"}


def sql(since: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        raise SystemExit(f"usefulness: --since must be YYYY-MM-DD, got {since!r}")
    return ("SELECT u.consumer, u.claim_id, u.surface, u.score, u.metadata, u.created_at, c.subject"
            " FROM claim_usages u LEFT JOIN memory_claims c ON c.id = u.claim_id"
            f" WHERE u.created_at >= '{since}' ORDER BY u.created_at, u.rowid")


def fetch(since: str) -> list[dict]:
    out = subprocess.run(["npx", "wrangler", "d1", "execute", "DB", "--remote", "--env", "production",
                          "--json", "--command", sql(since)], cwd=WORKER, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"usefulness: wrangler failed ({out.returncode}): {out.stderr.strip()[-500:]}")
    return json.loads(out.stdout)[0]["results"]


def band(rank: int | None) -> str:
    if rank is None:
        return "unplaced"
    return "1-3" if rank <= 3 else "4-10" if rank <= 10 else "11+"


def slot(row: dict) -> tuple[str, str, str]:
    meta = json.loads(row.get("metadata") or "{}")
    return (row["surface"], meta.get("section", "?"), band(meta.get("rank")))


def showings(rows: list[dict]) -> list[dict]:
    """One record per (session, claim) the session was shown: the slot it was first shown in, and the
    session's newest rating of it (None when it never rated it)."""
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:  # oldest first
        key = (r["consumer"], r["claim_id"])
        if r["surface"] == "feedback":
            if key in seen:  # a rating with no showing in the window can't be placed
                seen[key]["score"] = r["score"]
        elif key not in seen:
            seen[key] = {"claim": r["claim_id"], "subject": r.get("subject") or "", "slot": slot(r), "score": None}
    return list(seen.values())


def baselines(shown: list[dict]) -> dict[tuple, tuple[int, float]]:
    """Per slot: rated showings and their used rate."""
    by = defaultdict(list)
    for s in shown:
        if s["score"] is not None:
            by[s["slot"]].append(s["score"] == 1)
    return {k: (len(v), sum(v) / len(v)) for k, v in by.items()}


def lifts(shown: list[dict], base: dict, min_rated: int) -> list[dict]:
    by = defaultdict(list)
    for s in shown:
        if s["score"] is not None:
            by[s["claim"]].append(s)
    out = []
    for claim, rated in by.items():
        if len(rated) < min_rated:
            continue
        used = sum(s["score"] == 1 for s in rated) / len(rated)
        expected = sum(base[s["slot"]][1] for s in rated) / len(rated)
        out.append({"claim": claim, "subject": rated[0]["subject"], "n": len(rated), "used": used,
                    "expected": expected, "lift": used - expected,
                    "wrong": sum(s["score"] == -1 for s in rated)})
    return sorted(out, key=lambda r: (-r["lift"], r["claim"]))


def report(rows: list[dict], min_rated: int) -> str:
    shown = showings(rows)
    rated = [s for s in shown if s["score"] is not None]
    base = baselines(shown)
    lines = [f"{len(shown)} showings, {len(rated)} rated ({len(rated) / len(shown):.0%})" if shown else "no showings",
             "", "Used rate by slot:", f"  {'surface':9} {'section':9} {'rank':8} {'n':>4} {'used':>5}"]
    lines += [f"  {s:9} {sec:9} {b:8} {n:>4} {rate:>5.2f}" for (s, sec, b), (n, rate) in sorted(base.items())]
    table = lifts(shown, base, min_rated)
    lines += ["", f"Claims rated at least {min_rated} times, by lift (used rate minus its slots' rate):",
              f"  {'claim':10} {'n':>3} {'used':>5} {'expect':>6} {'lift':>6} {'wrong':>5}  subject"]
    lines += [f"  {r['claim'][:8]:10} {r['n']:>3} {r['used']:>5.2f} {r['expected']:>6.2f} {r['lift']:>+6.2f} "
              f"{r['wrong']:>5}  {r['subject'][:60]}" for r in table]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", default="2026-11-07", help="YYYY-MM-DD; feedback began on 2026-11-07")
    parser.add_argument("--min", type=int, default=3, help="ratings a claim needs to be listed")
    parser.add_argument("--from", dest="source", type=Path, help="rows as JSON, instead of querying D1")
    args = parser.parse_args(argv)
    rows = json.loads(args.source.read_text()) if args.source else fetch(args.since)
    print(report(rows, args.min))
    return 0


if __name__ == "__main__":
    sys.exit(main())
