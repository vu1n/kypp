#!/usr/bin/env python3
"""Agreement-judge eval: does Clef tell "same lesson, different words" from "different lesson"?

The corroboration gate promotes a candidate only when separate sessions agree. Grouping by exact
subject misses agreement (each LLM-distilled session names a lesson its own way), so the gate's
next step is: claims that share a tag are compared pairwise and a confident "same lesson" from Clef
counts as agreement. This builds the pairs, labels them and scores them; scripts/tune_cutoff.py picks
the cutoff from the saved scores.

    build  pairs of claims from different sessions that share a tag (here: the FrontierMemory task)
    label  Opus 5.5 labels each pair same | different | conflict (cached)
    score  Clef's P(agree) per pair through the production question, s1.agree_question (cached)

    uv run python eval/agreement/pairs.py build --db /tmp/fm/kypp.db --out eval/agreement/pairs.jsonl
    tumbler run -- uv run --with anthropic python eval/agreement/pairs.py label --pairs eval/agreement/pairs.jsonl
    tumbler run -- uv run python eval/agreement/pairs.py score --pairs eval/agreement/pairs.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import random
import re
import sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval", "frontiermemory"))

HERE = os.path.dirname(__file__)
LABEL_CACHE = os.path.join(HERE, "label_cache.json")
SCORE_CACHE = os.path.join(HERE, "clef_cache.json")


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]{3,}", s.lower()))


def build(db: str, out: str, near: int, rand: int, seed: int) -> None:
    os.environ["KYPP_MEMORY_DB"] = db
    from kypp.store import store_from_env
    from recall_check import task_of
    store = store_from_env()
    owner = task_of(store)
    cur = store.db.cursor()
    rows = cur.execute("SELECT id, subject, content, source_ids FROM memory_claims "
                       "WHERE status IN ('candidate', 'accepted')").fetchall()
    by_tag = defaultdict(list)
    for cid, subj, content, srcs in rows:
        if cid in owner:
            by_tag[owner[cid]].append({"id": cid, "subject": subj, "content": content,
                                       "source": (json.loads(srcs or "[]") or [""])[0]})
    rng = random.Random(seed)
    pairs = []
    for tag, cs in sorted(by_tag.items()):
        cand = [(a, b) for i, a in enumerate(cs) for b in cs[i + 1:] if a["source"] != b["source"]]
        tok = {c["id"]: _tokens(c["subject"] + " " + c["content"]) for c in cs}
        jac = lambda p: len(tok[p[0]["id"]] & tok[p[1]["id"]]) / (len(tok[p[0]["id"]] | tok[p[1]["id"]]) or 1)
        cand.sort(key=jac, reverse=True)
        chosen = cand[:near]
        rest = cand[near:]
        chosen += rng.sample(rest, min(rand, len(rest)))
        for a, b in chosen:
            pairs.append({"id": f"{a['id'][:8]}-{b['id'][:8]}", "kind": tag, "a": a, "b": b,
                          "near": (a, b) in cand[:near]})
    with open(out, "w") as f:
        f.writelines(json.dumps(p) + "\n" for p in pairs)
    print(f"{len(pairs)} pairs over {len(by_tag)} tags → {out}")


# Two rubrics. `redundant` (the first pass) asks whether one claim makes the other unnecessary; that is
# stricter than corroboration needs, and it marked "same point, one adds a detail" as different.
# `agree` asks what the gate asks: do two sessions independently support the same lesson?
RUBRICS = {"redundant": None, "agree": None}

LABEL_PROMPT = """You are labelling pairs of memory claims that coding agents wrote in separate sessions on the same task.
For each pair decide whether the two claims state the SAME lesson, so that either one could stand as the agreed answer and the other adds nothing a later agent would need.

- same: the same point, possibly in different words or with more or less detail.
- different: different points (they can both be true, but one cannot stand in for the other).
- conflict: they contradict each other on the same point.

Judge the lesson, not the topic: two claims about the same function that teach different things are "different".
Output ONLY a JSON object mapping each pair id to one of "same", "different", "conflict".

"""

AGREE_PROMPT = """You are labelling pairs of memory claims that coding agents wrote in separate sessions on the same task.
A shared memory accepts a lesson once two sessions independently support it. For each pair decide whether the two claims support the SAME main lesson.

- same: the same main point. One may add detail or examples, as long as nothing contradicts.
- different: different main points, e.g. a method versus a computed result, a structure versus its numbers, or two separate pitfalls about the same function.
- conflict: they disagree on the same point (different values, opposite advice).

Judge the main point, not the topic.
Output ONLY a JSON object mapping each pair id to one of "same", "different", "conflict".

"""
RUBRICS = {"redundant": LABEL_PROMPT, "agree": AGREE_PROMPT}


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def _read_pairs(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def label(path: str, model: str, rubric: str) -> None:
    import anthropic
    client = anthropic.Anthropic()
    pairs = _read_pairs(path)
    cache = _load(LABEL_CACHE)
    by_kind = defaultdict(list)
    for p in pairs:
        by_kind[p["kind"]].append(p)

    def one(kind):
        ps = by_kind[kind]
        body = "\n\n".join(f"## {p['id']}\nA: {p['a']['subject']} — {p['a']['content']}\n"
                           f"B: {p['b']['subject']} — {p['b']['content']}" for p in ps)
        prompt = RUBRICS[rubric] + body
        key = hashlib.sha256(f"{model}|{prompt}".encode()).hexdigest()
        if key in cache:
            return key, cache[key]
        r = client.messages.create(model=model, max_tokens=16000, output_config={"effort": "high"},
                                   messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        return key, {"labels": json.loads(m.group(0)), "in": r.usage.input_tokens, "out": r.usage.output_tokens}

    with cf.ThreadPoolExecutor(8) as ex:
        for key, val in ex.map(one, sorted(by_kind)):
            cache[key] = val
    json.dump(cache, open(LABEL_CACHE, "w"), indent=0, sort_keys=True)
    labels = {}
    for kind in by_kind:
        body = "\n\n".join(f"## {p['id']}\nA: {p['a']['subject']} — {p['a']['content']}\n"
                           f"B: {p['b']['subject']} — {p['b']['content']}" for p in by_kind[kind])
        labels.update(cache[hashlib.sha256(f"{model}|{RUBRICS[rubric] + body}".encode()).hexdigest()]["labels"])
    for p in pairs:
        p[f"label_{rubric}"] = labels.get(p["id"], "")
    with open(path, "w") as f:
        f.writelines(json.dumps(p) + "\n" for p in pairs)
    from collections import Counter
    print(Counter(p[f"label_{rubric}"] for p in pairs))


def score(path: str, out: str, rubric: str) -> None:
    from kypp.episodes import workers_ai_runner
    from kypp.s1 import agree_question
    run = workers_ai_runner(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"],
                            model="clef", key="questions")
    pairs = _read_pairs(path)
    cache = _load(SCORE_CACHE)

    def state(p):  # the production judge's state: arbiter hands it the supporting claims
        return {"claims": [{"subject": c["subject"], "content": c["content"]} for c in (p["a"], p["b"])]}

    def one(p):
        st = state(p)
        key = hashlib.sha256(json.dumps([st, agree_question()], sort_keys=True).encode()).hexdigest()
        if key not in cache:
            ans = run(st, {"q": agree_question()}).get("q") or {}
            cache[key] = ans.get("noul")
        return p, cache[key]

    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, pairs))
    json.dump(cache, open(SCORE_CACHE, "w"), indent=0, sort_keys=True)
    with open(out, "w") as f:
        for p, s in results:
            raw = p.get(f"label_{rubric}")
            if s is not None and raw:
                # conflict counts as "not the same": promoting on it is the worst mistake
                f.write(json.dumps({"id": p["id"], "kind": p["kind"], "near": p["near"],
                                    "label": "same" if raw == "same" else "not_same",
                                    "raw_label": raw, "p_agree": s}) + "\n")
    print(f"scored {sum(s is not None for _, s in results)}/{len(results)} → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "label", "score"])
    ap.add_argument("--db")
    ap.add_argument("--pairs", default=os.path.join(HERE, "pairs.jsonl"))
    ap.add_argument("--out")
    ap.add_argument("--near", type=int, default=25)
    ap.add_argument("--rand", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--rubric", choices=sorted(RUBRICS), default="agree")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.db, a.out or a.pairs, a.near, a.rand, a.seed)
    elif a.cmd == "label":
        label(a.pairs, a.model, a.rubric)
    else:
        score(a.pairs, a.out or os.path.join(HERE, f"scores_{a.rubric}.jsonl"), a.rubric)
