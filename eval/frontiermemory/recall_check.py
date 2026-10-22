#!/usr/bin/env python3
"""Offline retrieval check for the FrontierMemory store: no task agent, no verifier.

For each of the 15 task prompts, does kypp's governed recall return claims distilled from THAT
task's source trajectories? A claim's task is read from its provenance (claim → observation →
session id → task slug). Reported per surface the agent would use: briefing (accepted only),
recall (accepted only, the MCP default) and recall with candidates.

    uv run python eval/frontiermemory/recall_check.py --db /tmp/fm/kypp.db --logs /tmp/fm/events
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kypp.store import store_from_env  # noqa: E402
from kypp.view import briefing_claims  # noqa: E402

PROJECT = "frontiermemory"


def task_of(store) -> dict[str, str]:
    """claim id → task slug, via the observation each claim was distilled from."""
    cur = store.db.cursor()
    obs = {oid: (src or "") for oid, src in cur.execute("SELECT id, source FROM observations").fetchall()}
    out = {}
    for cid, srcs in cur.execute("SELECT id, source_ids FROM memory_claims").fetchall():
        for oid in json.loads(srcs or "[]"):
            sid = obs.get(oid, "").removeprefix("session:")
            if sid:
                out[cid] = sid.split("__", 1)[0]
    return out


def queries(logs: str) -> dict[str, dict[str, str]]:
    """Per task: the instruction the eval agent gets, and a short query an agent might type."""
    out = {}
    for m in json.load(open(os.path.join(logs, "index.json"))):
        t = m["fm_task"]
        if t not in out:
            words = re.findall(r"[A-Za-z0-9]+", m["task"])
            out[t] = {"instruction": m["task"], "short": " ".join(words[:8])}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out")
    a = ap.parse_args()
    os.environ["KYPP_MEMORY_DB"] = a.db
    store = store_from_env()
    owner = task_of(store)
    cur = store.db.cursor()
    status = collections.Counter(s for (s,) in cur.execute("SELECT status FROM memory_claims").fetchall())
    per_task_status = collections.defaultdict(collections.Counter)
    for cid, s in cur.execute("SELECT id, status FROM memory_claims").fetchall():
        per_task_status[owner.get(cid, "?")][s] += 1

    brief = briefing_claims(store, PROJECT, 12)
    rows = []
    for task, q in sorted(queries(a.logs).items()):
        row = {"task": task, "claims": dict(per_task_status[task])}
        for form, text in q.items():
            for cand in (False, True):
                hits = store.recall(text, project=PROJECT, include_candidates=cand, limit=a.k, ground=False)
                own = sum(owner.get(c.id) == task for c in hits)
                row[f"{form}{'+cand' if cand else ''}"] = {"returned": len(hits), "own": own}
        rows.append(row)
    report = {"status": dict(status), "briefing_tasks": collections.Counter(owner.get(c.id, "?") for c in brief),
              "rows": rows}
    if a.out:
        json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps(report["status"]), json.dumps(report["briefing_tasks"]))
    cols = ["instruction", "instruction+cand", "short", "short+cand"]
    print(f"{'task':52} {'claims (acc/cand)':>18} " + " ".join(f"{c:>17}" for c in cols))
    for r in rows:
        c = r["claims"]
        cells = " ".join(f"{r[k]['own']:>3}/{r[k]['returned']:<3} own/ret  "[:17].rjust(17) for k in cols)
        print(f"{r['task'][:52]:52} {c.get('accepted', 0):>8}/{c.get('candidate', 0):<9} {cells}")


if __name__ == "__main__":
    main()
