#!/usr/bin/env python3
"""report.py — what an agent would see from recall, with and without the rerank, on saved scores.

For each query it takes the keyword pool from scores.jsonl in keyword order, applies the rerank the
Worker applies (s1.rerank: drop below the floor, sort by score in 0.1 bands, keyword order within a
band, cut to --limit) and counts relevant and irrelevant claims shown. A relevant claim the keyword
search never pooled counts as missed by every arm. No model is called.

    python worker/bench/recall_rerank/report.py eval/recall_rerank/scores.jsonl eval/recall_rerank/queries.jsonl \
        --score p_rel --floor 0.25 [--score p_single --floor 0.3] [--limit 10] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

BAND = 0.1  # worker/src/s1.ts BAND


def rerank(pool: list[dict], score: str, floor: float, limit: int) -> list[dict]:
    kept = [r for r in pool if r[score] >= floor]
    kept.sort(key=lambda r: (-math.floor(r[score] / BAND), r["fts_rank"]))
    return kept[:limit]


def arm(queries: list[dict], pools: dict, shown_for, limit: int) -> dict:
    by_kind: dict[str, dict] = defaultdict(lambda: {"queries": 0, "relevant": 0, "found": 0, "shown": 0, "noise": 0, "first": 0.0, "answerable": 0, "empty": 0})
    for q in queries:
        shown = shown_for(pools.get(q["id"], []))[:limit]
        rel = set(q["relevant"])
        hits = [i for i, r in enumerate(shown) if r["claim"] in rel]
        k = by_kind[q["kind"]]
        k["queries"] += 1
        k["relevant"] += len(rel)
        k["found"] += len(hits)
        k["shown"] += len(shown)
        k["noise"] += len(shown) - len(hits)
        k["first"] += 1 / (hits[0] + 1) if hits else 0.0  # reciprocal rank of the first relevant claim
        k["answerable"] += bool(rel)
        k["empty"] += not shown
    out = {}
    total = {"queries": 0, "relevant": 0, "found": 0, "shown": 0, "noise": 0, "first": 0.0, "answerable": 0, "empty": 0}
    for kind, k in sorted(by_kind.items()):
        out[kind] = k
        for f in total:
            total[f] += k[f]
    out["all"] = total
    for k in out.values():
        first, answerable = k.pop("first"), k.pop("answerable")  # MRR over queries that have a relevant claim
        k["mrr"] = round(first / answerable, 3) if answerable else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scores")
    ap.add_argument("queries")
    ap.add_argument("--score", action="append", default=[])
    ap.add_argument("--floor", action="append", type=float, default=[])
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json")
    a = ap.parse_args()
    queries = [json.loads(l) for l in open(a.queries) if l.strip()]
    pools: dict[str, list[dict]] = defaultdict(list)
    for l in open(a.scores):
        if l.strip():
            r = json.loads(l)
            pools[r["query"]].append(r)
    for p in pools.values():
        p.sort(key=lambda r: r["fts_rank"])
    arms = {"keyword order": arm(queries, pools, lambda p: p, a.limit)}
    for score, floor in zip(a.score, a.floor):
        arms[f"{score} >= {floor}"] = arm(queries, pools, lambda p, s=score, f=floor: rerank(p, s, f, a.limit), a.limit)
    print(f"top {a.limit} per query; found = relevant claims shown, noise = irrelevant claims shown")
    print(f"{'arm':<18} {'kind':<11} {'found':>9} {'noise':>6} {'MRR':>6} {'empty':>6}")
    for name, kinds in arms.items():
        for kind, k in kinds.items():
            print(f"{name:<18} {kind:<11} {k['found']:>4}/{k['relevant']:<4} {k['noise']:>6} {k['mrr'] if k['mrr'] is not None else '-':>6} {k['empty']:>3}/{k['queries']}")
    if a.json:
        json.dump({"limit": a.limit, "arms": arms}, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
