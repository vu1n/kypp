"""Seed a replay Worker and replay the query set against it.
usage: python -I drive.py <base-url> <corpus.jsonl> <queries.json> <out.json> [--seed] [--batch N]
Writes {"queries": [{q, project, ids, server_ms, client_ms}], "seed_s": ...}.
"""
import json, sys, time, urllib.request

base, corpus, queries, out = sys.argv[1:5]
seed = "--seed" in sys.argv
batch = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 250

def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())

res = {}
if seed:
    rows = [json.loads(l) for l in open(corpus)]
    t0 = time.perf_counter()
    for i in range(0, len(rows), batch):
        post("/seed", {"claims": rows[i:i + batch]})
    res["seed_s"] = round(time.perf_counter() - t0, 2)
    print("seeded", len(rows), "in", res["seed_s"], "s", file=sys.stderr)
with urllib.request.urlopen(base + "/count") as r:
    res["count"] = json.loads(r.read())
qs = json.load(open(queries))
res["queries"] = []
for mode in ("none", "origin"):
    for q in qs:
        t0 = time.perf_counter()
        r = post("/recall", {**q, "candidates": mode})
        res["queries"].append({**q, "candidates": mode, "ids": r["ids"], "server_ms": r["ms"],
                               "client_ms": (time.perf_counter() - t0) * 1000})
json.dump(res, open(out, "w"), ensure_ascii=False)
ms = sorted(x["client_ms"] for x in res["queries"])
print("count", res["count"], "p50 %.1f p95 %.1f max %.1f ms" % (ms[len(ms) // 2], ms[int(len(ms) * .95)], ms[-1]), file=sys.stderr)
