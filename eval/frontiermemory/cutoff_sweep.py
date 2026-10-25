#!/usr/bin/env python3
"""Clef cutoff for the agreement gate on the FrontierMemory store: what each cutoff promotes, and how
often a pair it would join is not the same lesson.

Reads the Clef scores fuzzy_gate.py cached (no new Clef calls). For each cutoff it reruns the agreement
phase on a fresh copy of the dreamed store. Then it draws judged pairs stratified by score, has Opus
label them with eval/agreement's `agree` rubric, and writes {score, label} rows for
scripts/tune_cutoff.py.

    tumbler run -- uv run --with anthropic python eval/frontiermemory/cutoff_sweep.py --db /tmp/fm/kypp.db \
        --logs /tmp/fm/events --cache /tmp/fm/clef_cache.json --work /tmp/fm/sweep
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import random
import re
import shutil
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "agreement"))

from export_memory import PROJECT  # noqa: E402
from fuzzy_gate import topic_tags  # noqa: E402

CUTOFFS = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]
BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.85), (0.85, 0.9), (0.9, 0.95), (0.95, 1.01)]


def fresh(db: str, dst: str):
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.copy(db + ext, dst + ext)
    os.environ["KYPP_MEMORY_DB"] = dst
    from kypp.store import store_from_env
    return store_from_env()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--per-bin", type=int, default=30)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--out", default=os.path.join(HERE, "cutoff_sweep_results.json"))
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    from kypp import arbiter
    from kypp.s1 import agree_question
    from pairs import AGREE_PROMPT
    cache = json.load(open(a.cache))

    def key(pair):
        st = {"claims": [{"subject": c.subject, "content": c.content} for c in pair]}
        return hashlib.sha256(json.dumps([st, agree_question()], sort_keys=True).encode()).hexdigest()

    store = fresh(a.db, os.path.join(a.work, "probe.db"))
    tags = topic_tags(store, a.logs)
    judged = []
    arbiter.consolidate(store, project=PROJECT, dry_run=True, tag_of=lambda c: tags.get(c.id),
                        pair_judge=lambda pair: judged.append((pair, cache.get(key(pair)))))
    scored = [(p, s) for p, s in judged if s is not None]

    sweep = []
    for cut in CUTOFFS:
        st = fresh(a.db, os.path.join(a.work, f"cut{cut}.db"))
        res = arbiter.consolidate(st, project=PROJECT, tag_of=lambda c: tags.get(c.id),
                                  pair_judge=lambda pair: cache.get(key(pair)), agree_at=cut)
        sweep.append({"cutoff": cut, "pairs_at_or_above": sum(s >= cut for _, s in scored),
                      "accepted": sum(c.status == "accepted" for c in st.live_claims(PROJECT)),
                      "superseded": res["superseded"]})

    rng = random.Random(7)
    sample = []
    for lo, hi in BINS:
        inbin = [(p, s) for p, s in scored if lo <= s < hi]
        sample += [(f"b{lo}-{i}", p, s) for i, (p, s) in enumerate(rng.sample(inbin, min(a.per_bin, len(inbin))))]
    import anthropic
    client = anthropic.Anthropic()

    def label(chunk):
        body = "\n\n".join(f"## {pid}\nA: {p[0].subject} — {p[0].content}\nB: {p[1].subject} — {p[1].content}"
                           for pid, p, _ in chunk)
        r = client.messages.create(model=a.model, max_tokens=16000, output_config={"effort": "high"},
                                   messages=[{"role": "user", "content": AGREE_PROMPT + body}])
        text = "".join(b.text for b in r.content if b.type == "text")
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    labels = {}
    with cf.ThreadPoolExecutor(4) as ex:
        for got in ex.map(label, [sample[i:i + 30] for i in range(0, len(sample), 30)]):
            labels.update(got)
    rows = [{"id": pid, "score": s, "label": labels.get(pid, ""), "kind": f"{s:.2f}"[:3]} for pid, _, s in sample]
    with open(os.path.join(a.work, "labelled.jsonl"), "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    by_bin = []
    for lo, hi in BINS:
        b = [r for r in rows if lo <= r["score"] < hi]
        by_bin.append({"bin": f"{lo}-{min(hi, 1.0)}", "n": len(b), "same": sum(r["label"] == "same" for r in b),
                       "conflict": sum(r["label"] == "conflict" for r in b)})
    report = {"pairs_judged": len(judged), "pairs_scored": len(scored), "sweep": sweep, "labels_by_bin": by_bin}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
