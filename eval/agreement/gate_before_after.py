#!/usr/bin/env python3
"""Before/after for the agreement phase on the FrontierMemory store.

Before: the store as dream.py left it (exact-subject consolidation). After: a copy, consolidated
again with tag_of = Clef's tag for each claim (tagging.py --tags-out) and pair_judge = Clef through
the production agreement question. Reports what was promoted, and how many promoted clusters hold a
pair the labelled set (pairs.jsonl, rubric `agree`) calls not-the-same: the false-merge check.

    tumbler run -- uv run python eval/agreement/gate_before_after.py --db /tmp/fm/kypp.db \
        --tags /tmp/fm/tags.json --after /tmp/fm/kypp-after.db
"""
import argparse
import concurrent.futures as cf
import hashlib
import itertools
import json
import os
import shutil
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", ".."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--tags", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "gate_results.json"))
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
    tags = json.load(open(a.tags))
    run = workers_ai_runner(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"],
                            model="clef", key="questions")
    cache_path = os.path.join(HERE, "clef_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    def state(pair):
        return {"claims": [{"subject": c.subject, "content": c.content} for c in pair]}

    def key(pair):
        return hashlib.sha256(json.dumps([state(pair), agree_question()], sort_keys=True).encode()).hexdigest()

    # Warm the cache in parallel over exactly the pairs consolidate will ask about, then run it serially.
    asked = []
    arbiter.consolidate(store, project="frontiermemory", dry_run=True, tag_of=lambda c: tags.get(c.id),
                        pair_judge=lambda pair: asked.append(pair))

    def fill(pair):
        k = key(pair)
        if k not in cache:
            try:
                cache[k] = (run(state(pair), {"q": agree_question()}).get("q") or {}).get("noul")
            except Exception:  # noqa: BLE001 — no opinion; consolidate joins nothing on None
                cache[k] = None
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(fill, asked))
    json.dump(cache, open(cache_path, "w"), indent=0, sort_keys=True)

    res = arbiter.consolidate(store, project="frontiermemory", tag_of=lambda c: tags.get(c.id),
                              pair_judge=lambda pair: cache.get(key(pair)))
    agreed = [p for p in res["plan"] if p.get("agreement")]
    labels = {}
    for line in open(os.path.join(HERE, "pairs.jsonl")):
        p = json.loads(line)
        labels[frozenset((p["a"]["id"], p["b"]["id"]))] = p.get("label_agree")
    labelled = bad = 0
    for p in agreed:
        members = [p["survivor"], *p["superseded"]]
        for x, y in itertools.combinations(members, 2):
            lab = labels.get(frozenset((x, y)))
            if lab:
                labelled += 1
                bad += lab != "same"
    report = {"pairs_judged": len(asked), "agreement_clusters": len(agreed),
              "promoted": res["promoted"], "superseded": res["superseded"],
              "cluster_sizes": sorted((1 + len(p["superseded"]) for p in agreed), reverse=True),
              "labelled_pairs_inside_clusters": labelled, "of_which_not_same": bad}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
