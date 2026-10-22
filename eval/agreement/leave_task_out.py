#!/usr/bin/env python3
"""Leave-one-tag-out check for the agreement cutoff: tune on 14 tags, score the 15th.

tune_cutoff.py folds rows at random, so every fold has seen pairs from the held-out row's tag. This
checks the cutoff transfers to a tag it never saw, which is the case that matters for a new repo.

    python eval/agreement/leave_task_out.py eval/agreement/scores_agree.jsonl
"""
import json
import sys
from collections import defaultdict

COST_WRONG, COST_MISSED, MAX_WRONG = 10, 1, 0.02
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
grid = [i / 100 for i in range(101)]


def conf(rs, t):
    c = defaultdict(int)
    for r in rs:
        act, y = r["p_agree"] >= t, r["label"] == "same"
        c["caught" if act and y else "wrong" if act else "missed" if y else "spared"] += 1
    return c


def pick(rs):
    best = None
    for t in grid:
        c = conf(rs, t)
        neg = c["wrong"] + c["spared"]
        if neg and c["wrong"] / neg > MAX_WRONG:
            continue
        cost = COST_WRONG * c["wrong"] + COST_MISSED * c["missed"]
        if best is None or cost < best[0]:
            best = (cost, t)
    return best[1]


tot = defaultdict(int)
for tag in sorted({r["kind"] for r in rows}):
    t = pick([r for r in rows if r["kind"] != tag])
    c = conf([r for r in rows if r["kind"] == tag], t)
    for k, v in c.items():
        tot[k] += v
    print(f"{tag[:48]:48} cutoff {t:.2f}  caught {c['caught']:>2} wrong {c['wrong']:>2} missed {c['missed']:>2}")
neg, pos = tot["wrong"] + tot["spared"], tot["caught"] + tot["missed"]
print(f"held-out total: precision {tot['caught'] / max(1, tot['caught'] + tot['wrong']):.3f}  "
      f"catch {tot['caught'] / pos:.3f}  wrong rate {tot['wrong'] / neg:.3f}")
