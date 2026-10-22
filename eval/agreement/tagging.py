#!/usr/bin/env python3
"""Tagging check: can Clef put each claim under the right tag from a closed list?

The gate compares only claims that share a tag. In a repo the tags are the Brief feature map's
features; FrontierMemory has no feature map, so the tags here are the 15 tasks, each described by
its task page's one-line summary. Gold is the task the claim was distilled from. A claim Clef files
under the wrong tag can't corroborate (it is compared with the wrong claims), so tagging errors cost
recall, not precision.

    tumbler run -- uv run python eval/agreement/tagging.py --db /tmp/fm/kypp.db --site /tmp/fm/site
"""
import argparse
import collections
import concurrent.futures as cf
import glob
import html
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval", "frontiermemory"))


def tag_descriptions(site: str) -> dict[str, str]:
    out = {}
    for f in glob.glob(os.path.join(site, "traces", "*.html")):
        tag = os.path.basename(f).split("__", 1)[0]
        if tag in out:
            continue
        s = open(f).read()
        title = re.search(r"<h1>(.*?)</h1>", s, re.S)
        desc = re.search(r'</div>\s*(?:\s*)*<p>(.*?)</p></header>', s, re.S)
        out[tag] = html.unescape(f"{title.group(1) if title else tag}: {desc.group(1) if desc else ''}").strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "tagging_results.json"))
    ap.add_argument("--tags-out")
    a = ap.parse_args()
    os.environ["KYPP_MEMORY_DB"] = a.db
    from kypp.episodes import MAX_QUESTIONS, workers_ai_runner
    from kypp.store import store_from_env
    from recall_check import task_of
    store = store_from_env()
    owner = task_of(store)
    rows = [r for r in store.db.cursor().execute(
        "SELECT id, subject, content FROM memory_claims WHERE status IN ('candidate','accepted')").fetchall()
        if r[0] in owner]
    tags = tag_descriptions(a.site)
    names = sorted(tags)
    keys = {f"t{i}": n for i, n in enumerate(names)}
    question = {"type": "choice", "criteria": {k: tags[n] for k, n in keys.items()}}
    run = workers_ai_runner(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"],
                            model="clef", key="questions", timeout=120)

    def batch(chunk):
        state = {"claims": [{"id": f"c{j}", "subject": s, "content": c} for j, (_, s, c) in enumerate(chunk)]}
        qs = {f"c{j}": {**question, "instructions": f"The state lists lessons coding agents wrote to shared "
                        f"memory. Which task is the lesson of claim c{j} about?"} for j in range(len(chunk))}
        ans = run(state, qs)
        return [(cid, (ans.get(f"c{j}") or {}).get("choice"), (ans.get(f"c{j}") or {}).get("confidence"))
                for j, (cid, _, _) in enumerate(chunk)]

    size = min(MAX_QUESTIONS, 16)  # smaller states keep each claim's text inside Clef's window
    chunks = [rows[i:i + size] for i in range(0, len(rows), size)]
    with cf.ThreadPoolExecutor(4) as ex:
        got = [x for part in ex.map(batch, chunks) for x in part]
    right = sum(keys.get(ch) == owner[cid] for cid, ch, _ in got)
    per = collections.defaultdict(lambda: [0, 0])
    for cid, ch, _ in got:
        per[owner[cid]][0] += keys.get(ch) == owner[cid]
        per[owner[cid]][1] += 1
    report = {"claims": len(got), "correct": right, "accuracy": round(right / len(got), 3),
              "unanswered": sum(ch is None for _, ch, _ in got),
              "per_tag": {t: {"correct": c, "n": n} for t, (c, n) in sorted(per.items())}}
    json.dump(report, open(a.out, "w"), indent=1)
    if a.tags_out:  # claim id → Clef's tag, for the gate's tag_of in gate_before_after.py
        json.dump({cid: keys.get(ch) for cid, ch, _ in got}, open(a.tags_out, "w"))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
