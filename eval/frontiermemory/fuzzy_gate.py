#!/usr/bin/env python3
"""Agreement-gate consolidation on a dreamed FrontierMemory store, with tags the memory derives itself.

The agreement phase (arbiter.consolidate tag_of + pair_judge) compares candidates that share a tag and
lets Clef's "same lesson?" score corroborate. eval/agreement tagged claims with the 15 target tasks,
which FrontierMemory's rules keep from the memory agent. Here a claim's tag is the topic of the
trajectory it was distilled from: its own prompt, past boilerplate (fm_corpus.task_of), the same key
export_memory.py files topics by. Runs on a copy of the store; the original stays as dreamed.

    tumbler run -- uv run python eval/frontiermemory/fuzzy_gate.py --db /tmp/fm/kypp.db \
        --logs /tmp/fm/events --after /tmp/fm/kypp-gated.db --cache /tmp/fm/clef_cache.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from export_memory import PROJECT, _topic_key  # noqa: E402
from fm_corpus import task_of  # noqa: E402


def topic_tags(store, logs: str) -> dict[str, str]:
    """claim id → its source trajectory's topic key."""
    topic = {}
    for m in json.load(open(os.path.join(logs, "index.json"))):
        with open(os.path.join(logs, m["session_id"] + ".jsonl")) as fh:
            topic[m["session_id"]] = _topic_key(task_of([json.loads(x) for x in fh if x.strip()]))
    cur = store.db.cursor()
    obs = {oid: (src or "").removeprefix("session:") for oid, src in cur.execute("SELECT id, source FROM observations").fetchall()}
    tags = {}
    for cid, srcs in cur.execute("SELECT id, source_ids FROM memory_claims").fetchall():
        sid = next((obs.get(o) for o in json.loads(srcs or "[]") if obs.get(o) in topic), None)
        if sid:
            tags[cid] = topic[sid]
    return tags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "fuzzy_gate_results.json"))
    a = ap.parse_args()
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(a.db + ext):
            shutil.copy(a.db + ext, a.after + ext)
    os.environ["KYPP_MEMORY_DB"] = a.after
    from kypp import arbiter
    from kypp.episodes import workers_ai_runner
    from kypp.s1 import agree_question
    from kypp.store import store_from_env
    store = store_from_env()
    tags = topic_tags(store, a.logs)
    run = workers_ai_runner(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"],
                            model="clef", key="questions")
    cache = json.load(open(a.cache)) if os.path.exists(a.cache) else {}

    def state(pair):
        return {"claims": [{"subject": c.subject, "content": c.content} for c in pair]}

    def key(pair):
        return hashlib.sha256(json.dumps([state(pair), agree_question()], sort_keys=True).encode()).hexdigest()

    before = sum(c.status == "accepted" for c in store.live_claims(PROJECT))
    asked = []
    arbiter.consolidate(store, project=PROJECT, dry_run=True, tag_of=lambda c: tags.get(c.id),
                        pair_judge=lambda pair: asked.append(pair))
    errors = []

    def fill(pair):
        k = key(pair)
        if k in cache:
            return
        try:
            cache[k] = (run(state(pair), {"q": agree_question()}).get("q") or {}).get("noul")
        except Exception as e:  # noqa: BLE001 — no opinion; consolidate joins nothing on None
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            cache[k] = None
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(fill, asked))
    json.dump(cache, open(a.cache, "w"))

    res = arbiter.consolidate(store, project=PROJECT, tag_of=lambda c: tags.get(c.id),
                              pair_judge=lambda pair: cache.get(key(pair)))
    agreed = [p for p in res["plan"] if p.get("agreement")]
    after = sum(c.status == "accepted" for c in store.live_claims(PROJECT))
    report = {"tags": len(set(tags.values())), "pairs_judged": len(asked), "judge_errors": len(errors),
              "error_sample": errors[:3], "accepted_before": before, "accepted_after": after,
              "promoted": res["promoted"], "superseded": res["superseded"],
              "agreement_clusters": len(agreed),
              "cluster_sizes": sorted((1 + len(p["superseded"]) for p in agreed), reverse=True)[:30]}
    json.dump({**report, "clusters": agreed}, open(a.out, "w"), indent=1, default=str)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
