#!/usr/bin/env python3
"""tune_cutoff.py — pick a System One gate's cutoff from saved scores and stated costs, never by eye.

The method of Husain and Saha, "How To Use Decision Models for Evals" (hamel.dev, 2026-10-20):
state the minimums first, choose the cutoff on training folds of a repeated stratified 5-fold split,
report it on the held-out folds, and check calibration (Brier, reliability bins, Platt scaling) before
reading a score as a probability. It reads a JSONL of saved scores, so no model is called.

A gate acts when the score is below the cutoff (--act-below, e.g. the write gate drops when
p_keep < cutoff) or at/above it (--act-above). Rows whose label equals --act-label should be acted on.
Two mistakes, each with a cost:
  wrong act   the gate acts on a row it should have left alone (the write gate drops a real lesson)
  missed act  the gate leaves a row it should have acted on (the write gate stores junk)
Each training fold picks the cutoff with the lowest total cost among those meeting the minimums on
that fold (ties go to the cutoff that acts less); with none feasible, the fold is marked infeasible and
takes the lowest-cost cutoff anyway, as the post does.

    python scripts/tune_cutoff.py eval/write_gate/scores.jsonl --score p_keep --act-below \
        --act-label drop --cost-wrong 10 --cost-missed 1 --max-wrong-rate 0.02 \
        [--fixed 0.1 --fixed 0.25] [--test other_scores.jsonl --test-cutoff 0.25] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict


def load(path: str, score: str, act_label: str) -> list[tuple[float, int, str]]:
    """(score, 1 if the row should be acted on, kind) per row."""
    rows = []
    for line in open(path):
        if line.strip():
            r = json.loads(line)
            rows.append((float(r[score]), int(r["label"] == act_label), r.get("kind", "")))
    return rows


def acts(s: float, t: float, below: bool) -> bool:
    return s < t if below else s >= t


def confusion(rows, t: float, below: bool) -> dict:
    c = {"wrong": 0, "missed": 0, "caught": 0, "spared": 0}
    for s, y, _ in rows:
        a = acts(s, t, below)
        c["caught" if a and y else "wrong" if a else "missed" if y else "spared"] += 1
    return c


def cost(c: dict, a: argparse.Namespace) -> float:
    return a.cost_wrong * c["wrong"] + a.cost_missed * c["missed"]


def feasible(c: dict, a: argparse.Namespace) -> bool:
    negatives, acted = c["wrong"] + c["spared"], c["wrong"] + c["caught"]
    if negatives and c["wrong"] / negatives > a.max_wrong_rate:
        return False
    return not (a.min_precision and acted and c["caught"] / acted < a.min_precision)


def candidates(rows) -> list[float]:
    """Midpoints between adjacent distinct scores, plus both ends: every distinct decision."""
    xs = sorted({s for s, _, _ in rows})
    return [0.0] + [(x + y) / 2 for x, y in zip(xs, xs[1:])] + [1.0 + 1e-9]


def choose(rows, a: argparse.Namespace) -> tuple[float, bool]:
    best = None
    for t in candidates(rows):
        c = confusion(rows, t, a.below)
        # Rank feasible first, then cost, then fewer acts (the conservative side of a tie).
        key = (not feasible(c, a), cost(c, a), c["wrong"] + c["caught"])
        if best is None or key < best[0]:
            best = (key, t)
    return best[1], not best[0][0]


def folds(rows, k: int, rng: random.Random) -> list[list[int]]:
    """Stratified: each fold gets its share of each label."""
    out = [[] for _ in range(k)]
    for y in (0, 1):
        idx = [i for i, r in enumerate(rows) if r[1] == y]
        rng.shuffle(idx)
        for j, i in enumerate(idx):
            out[j % k].append(i)
    return out


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def platt(xs: list[float], ys: list[int]) -> tuple[float, float]:
    """Fit P(y=1) = sigmoid(a*logit(p) + b) by Newton's method on the log loss."""
    a = b = 0.0
    zs = [logit(x) for x in xs]
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for z, y in zip(zs, ys):
            q = 1 / (1 + math.exp(-(a * z + b)))
            w = q * (1 - q)
            ga += (q - y) * z; gb += q - y
            haa += w * z * z; hab += w * z; hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def brier(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def reliability(ps, ys, bins: int = 5) -> list[dict]:
    """Equal-count bins by predicted probability: mean predicted vs observed rate."""
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out = []
    for b in range(bins):
        idx = order[b * len(order) // bins:(b + 1) * len(order) // bins]
        if idx:
            out.append({"n": len(idx), "predicted": round(sum(ps[i] for i in idx) / len(idx), 3),
                        "observed": round(sum(ys[i] for i in idx) / len(idx), 3)})
    return out


def rates(c: dict) -> dict:
    neg, pos = c["wrong"] + c["spared"], c["caught"] + c["missed"]
    acted = c["wrong"] + c["caught"]
    return {**c, "wrong_rate": round(c["wrong"] / neg, 4) if neg else None,
            "catch_rate": round(c["caught"] / pos, 4) if pos else None,
            "precision": round(c["caught"] / acted, 4) if acted else None}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scores")
    ap.add_argument("--score", default="p")
    side = ap.add_mutually_exclusive_group(required=True)
    side.add_argument("--act-below", dest="below", action="store_true")
    side.add_argument("--act-above", dest="below", action="store_false")
    ap.add_argument("--act-label", required=True, help="label of rows the gate should act on")
    ap.add_argument("--cost-wrong", type=float, required=True)
    ap.add_argument("--cost-missed", type=float, required=True)
    ap.add_argument("--max-wrong-rate", type=float, default=1.0, help="minimum: wrong acts / rows to leave alone")
    ap.add_argument("--min-precision", type=float, default=0.0, help="minimum: right acts / acts")
    ap.add_argument("--fixed", type=float, action="append", default=[], help="also report this cutoff held out")
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--test", help="a separate scores file the tuning never saw")
    ap.add_argument("--test-cutoff", type=float, action="append", default=[])
    ap.add_argument("--json", help="write the report here too")
    a = ap.parse_args(argv)

    rows = load(a.scores, a.score, a.act_label)
    rng = random.Random(a.seed)
    held = defaultdict(lambda: {"wrong": 0, "missed": 0, "caught": 0, "spared": 0})
    chosen, infeasible = [], 0
    raw_b = cal_b = 0.0
    for _ in range(a.repeats):
        for test_idx in folds(rows, 5, rng):
            ts = set(test_idx)
            train = [r for i, r in enumerate(rows) if i not in ts]
            test = [rows[i] for i in test_idx]
            t, ok = choose(train, a)
            chosen.append(t)
            infeasible += not ok
            for name, cut in [("tuned", t)] + [(f"fixed {f}", f) for f in a.fixed]:
                for k, v in confusion(test, cut, a.below).items():
                    held[name][k] += v
            # Calibration of the score as P(act), so a gate that acts below reads 1 - score.
            p = (lambda s: 1 - s) if a.below else (lambda s: s)
            pa, pb = platt([p(s) for s, _, _ in train], [y for _, y, _ in train])
            ys = [y for _, y, _ in test]
            raw_b += brier([p(s) for s, _, _ in test], ys) * len(test)
            cal_b += brier([1 / (1 + math.exp(-(pa * logit(p(s)) + pb))) for s, _, _ in test], ys) * len(test)

    n_held = len(rows) * a.repeats
    chosen.sort()
    p = (lambda s: 1 - s) if a.below else (lambda s: s)
    full_t, full_ok = choose(rows, a)
    kinds = defaultdict(list)
    for s, y, kind in rows:
        kinds[(y, kind)].append(s)
    report = {
        "rows": len(rows), "act_rows": sum(y for _, y, _ in rows),
        "settings": {k: v for k, v in vars(a).items() if k not in ("scores", "json", "test", "test_cutoff")},
        "cv": {
            "splits": len(chosen), "infeasible_splits": infeasible,
            "cutoff_mean": round(sum(chosen) / len(chosen), 4),
            "cutoff_p10_p50_p90": [round(chosen[int(q * (len(chosen) - 1))], 4) for q in (0.1, 0.5, 0.9)],
            "held_out": {name: {**rates(c), "cost_per_100": round(100 * cost(c, a) / n_held, 2)} for name, c in held.items()},
            "brier_raw": round(raw_b / n_held, 4), "brier_platt": round(cal_b / n_held, 4),
        },
        "full_set": {"cutoff": round(full_t, 4), "feasible": full_ok},
        "reliability": reliability([p(s) for s, _, _ in rows], [y for _, y, _ in rows]),
        "by_kind": {f"{'act' if y else 'leave'}:{k}": {"n": len(v), "min": round(min(v), 3), "max": round(max(v), 3)}
                    for (y, k), v in sorted(kinds.items())},
    }
    if a.test:
        test = load(a.test, a.score, a.act_label)
        report["test"] = {"rows": len(test), "act_rows": sum(y for _, y, _ in test),
                          "brier_raw": round(brier([p(s) for s, _, _ in test], [y for _, y, _ in test]), 4),
                          "cutoffs": {str(t): rates(confusion(test, t, a.below)) for t in a.test_cutoff}}
    print(json.dumps(report, indent=2))
    if a.json:
        json.dump(report, open(a.json, "w"), indent=2)
        open(a.json, "a").write("\n")
    return report


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
