#!/usr/bin/env python3
"""A dreamed kypp store → a FrontierMemory memory folder (MEMORY.md + topic files).

FrontierMemory's fixed task agents read a folder of text, not an MCP server, so this renders what kypp
would hand an agent: the session-start briefing (accepted only, pitfalls first), then every live claim
filed under the task prompt of the trajectory it was distilled from. Grouping uses each trajectory's
own first prompt (what the memory agent can see), never the benchmark's target-task list. Accepted
claims come before candidates in each file and are marked, so the agent can weigh them. No model call:
the rendering is deterministic.

    uv run python eval/frontiermemory/export_memory.py --db /tmp/fm/kypp.db --logs /tmp/fm/events --out /tmp/fm/memory
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kypp.store import store_from_env  # noqa: E402
from kypp.view import briefing_claims, render_claims  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from fm_corpus import task_of  # noqa: E402

PROJECT = "frontiermemory"

HEADER = """# Memory

Lessons a memory system (kypp) distilled from earlier agents' attempts at tasks, each graded PASS or
FAIL by a verifier. **Before you start, find the topic below whose prompt matches your task and read
its file** under `topics/`: it lists what earlier attempts learned, especially what made them fail.
`[accepted]` lessons were corroborated by more than one attempt; `[candidate]` lessons come from a
single attempt and may be wrong. Several topics can share a repo, company or tool: read every match.
"""


def _topic_key(task: str) -> str:
    return re.sub(r"\s+", " ", task).strip()[:160]


def _slug(task: str, taken: set[str]) -> str:
    words = re.findall(r"[a-z0-9]+", task.lower())
    base = "-".join(w for w in words if len(w) > 2)[:48].strip("-") or "topic"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


def _line(c) -> str:
    content = re.sub(r"\s+", " ", c.content).strip()
    return f"- [{c.status}] ({c.type}) **{c.subject}**: {content}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ["KYPP_MEMORY_DB"] = a.db
    store = store_from_env()
    index = {m["session_id"]: m for m in json.load(open(os.path.join(a.logs, "index.json")))}
    for sid, m in index.items():  # the prompt past harness boilerplate, so topics group by the real ask
        with open(os.path.join(a.logs, sid + ".jsonl")) as fh:
            m["task"] = task_of([json.loads(line) for line in fh if line.strip()])
    cur = store.db.cursor()
    obs = {oid: (src or "").removeprefix("session:")
           for oid, src in cur.execute("SELECT id, source FROM observations").fetchall()}

    runs = collections.defaultdict(lambda: [0, 0])  # topic → [pass, fail]
    prompt = {}
    for m in index.values():
        k = _topic_key(m["task"])
        prompt.setdefault(k, m["task"])
        runs[k][m["outcome"] != "pass"] += 1
    claims = store.live_claims(PROJECT)
    by_topic = collections.defaultdict(list)
    for c in claims:
        sids = [obs.get(o, "") for o in c.source_ids]
        k = next((_topic_key(index[s]["task"]) for s in sids if s in index), "(unattributed)")
        by_topic[k].append(c)

    shutil.rmtree(a.out, ignore_errors=True)
    os.makedirs(os.path.join(a.out, "topics"))
    taken: set[str] = set()
    rows = []
    for k, cs in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        cs.sort(key=lambda c: (c.status != "accepted", -c.confidence))
        slug = _slug(prompt.get(k, k), taken)
        p, f = runs.get(k, [0, 0])
        body = [f"# {slug}", "", f"Earlier attempts: {p} passed, {f} failed.", "", "## Prompt those attempts got",
                "", "```", prompt.get(k, k)[:1500], "```", "", "## Lessons", ""] + [_line(c) for c in cs]
        with open(os.path.join(a.out, "topics", slug + ".md"), "w") as fh:
            fh.write("\n".join(body) + "\n")
        acc = sum(c.status == "accepted" for c in cs)
        gist = re.sub(r"\s+", " ", prompt.get(k, k)).strip()[:150]
        rows.append(f"- `topics/{slug}.md` ({len(cs)} lessons, {acc} accepted; {p} pass / {f} fail): {gist}")

    brief = render_claims(briefing_claims(store, PROJECT, 12), empty="(no accepted memory yet)")
    text = "\n".join([HEADER, "## Briefing (strongest accepted lessons, traps first)", "", brief, "",
                      "## Topics", ""] + rows) + "\n"
    with open(os.path.join(a.out, "MEMORY.md"), "w") as fh:
        fh.write(text)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(a.out) for f in fs)
    print(json.dumps({"topics": len(rows), "claims": len(claims), "bytes": size,
                      "accepted": sum(c.status == "accepted" for c in claims)}))


if __name__ == "__main__":
    main()
