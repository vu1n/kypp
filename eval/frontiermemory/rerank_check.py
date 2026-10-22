#!/usr/bin/env python3
"""recall_check.py with the hosted Worker's rerank: what an agent on the hosted store would see.

Mirrors `worker/src/s1.ts` rankForQuery: the keyword search pulls RERANK_POOL claims, Clef scores
each one alone with the Worker's relevance question, claims under RELEVANT_FLOOR are dropped, and
scores within one BAND keep the keyword order. The pool comes from the local libsql FTS5 store, not
D1, so the pool itself can differ slightly from hosted (eval/../worker/bench/replay).

    tumbler run -- uv run python eval/frontiermemory/rerank_check.py --db /tmp/fm/kypp.db --logs /tmp/fm/events
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Copied from worker/src/s1.ts; change them together.
RERANK_POOL, RERANK_QUERY, RELEVANT_FLOOR, BAND = 40, 500, 0.3, 0.1
RELEVANT_CRITERIA = {
    "true": "Reading it would help with what the query asks: it is about the same problem, tool, procedure or preference.",
    "false": "It is about something else, even if it shares a word with the query.",
}
PROJECT = "frontiermemory"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--cache", default=os.path.join(HERE, "rerank_cache.json"))
    ap.add_argument("--out")
    a = ap.parse_args()
    os.environ["KYPP_MEMORY_DB"] = a.db
    from kypp.episodes import workers_ai_runner
    from kypp.store import store_from_env
    from recall_check import queries, task_of
    store = store_from_env()
    owner = task_of(store)
    run = workers_ai_runner(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"],
                            model="clef", key="questions")
    cache = json.load(open(a.cache)) if os.path.exists(a.cache) else {}

    def score(query, c):
        state = {"query": query[:RERANK_QUERY], "claims": [{"n": 0, "subject": c.subject, "content": c.content}]}
        q = {"c0": {"type": "noul", "instructions": "Is claim 0 in the state relevant to the query?",
                    "criteria": RELEVANT_CRITERIA}}
        k = hashlib.sha256(json.dumps([state, q], sort_keys=True).encode()).hexdigest()
        if k not in cache:
            try:
                cache[k] = (run(state, q).get("c0") or {}).get("noul")
            except Exception:  # noqa: BLE001 — no opinion: the Worker keeps keyword order then
                cache[k] = None
        return cache[k]

    rows = []
    for task, q in sorted(queries(a.logs).items()):
        row = {"task": task}
        for cand in (False, True):
            pool = store.recall(q["instruction"], project=PROJECT, include_candidates=cand,
                                limit=RERANK_POOL, ground=False)
            with cf.ThreadPoolExecutor(8) as ex:
                scores = list(ex.map(lambda c: score(q["instruction"], c), pool))
            if any(s is None or not math.isfinite(s) for s in scores):
                ranked = pool[:10]
            else:
                kept = [(c, i, s) for i, (c, s) in enumerate(zip(pool, scores)) if s >= RELEVANT_FLOOR]
                kept.sort(key=lambda x: (-math.floor(x[2] / BAND), x[1]))
                ranked = [c for c, _, _ in kept[:10]]
            key = "rerank+cand" if cand else "rerank"
            row[key] = {"returned": len(ranked), "own": sum(owner.get(c.id) == task for c in ranked),
                        "keyword_own": sum(owner.get(c.id) == task for c in pool[:10])}
        rows.append(row)
    json.dump(cache, open(a.cache, "w"), indent=0, sort_keys=True)
    if a.out:
        json.dump(rows, open(a.out, "w"), indent=1)
    for k in ("rerank", "rerank+cand"):
        print(k, "own@10", sum(r[k]["own"] for r in rows), "/ returned", sum(r[k]["returned"] for r in rows),
              "| keyword own@10", sum(r[k]["keyword_own"] for r in rows),
              "| tasks with none", sum(r[k]["own"] == 0 for r in rows))
    for r in rows:
        print(f"  {r['task'][:44]:44} " + "  ".join(f"{k} {r[k]['own']}/{r[k]['returned']}" for k in ("rerank", "rerank+cand")))


if __name__ == "__main__":
    main()
