"""Replay the query set against a Python kypp store (run from the kypp checkout you want to test).
usage: python -I py_replay.py <turso|sqlite|libsql> <db-path> <corpus.jsonl> <queries.json> <out.json>
`turso` is main's MemoryStore (pyturso, LIKE keyword); `sqlite`/`libsql` are PR #33's adapters.
"""
import json, os, sys, time
sys.path.insert(0, os.getcwd())
kind, path, corpus, queries, out = sys.argv[1:6]
if kind == "turso":
    from kypp.store import MemoryStore as S
    fts = False
else:
    from kypp.backends import SqliteFtsStore, LibsqlStore
    S = SqliteFtsStore if kind == "sqlite" else LibsqlStore
    fts = True

s = S(path)
rows = [json.loads(l) for l in open(corpus)]
t0 = time.perf_counter()
for i in range(0, len(rows), 500):
    stmts = []
    for c in rows[i:i + 500]:
        stmts.append(("INSERT INTO memory_claims(id,type,subject,content,scope,project,agent,user,status,authority,confidence,"
                      "source_ids,code_refs,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,'replay',NULL,?,'agent',?,'[]','[]','{}',?,?)",
                      (c["id"], c["type"], c["subject"], c["content"], c["scope"], c["project"], c["status"], c["confidence"], c["ts"], c["ts"])))
        if fts:
            stmts.append(("INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)", (c["id"], c["subject"], c["content"])))
    s._write(stmts)
seed_s = time.perf_counter() - t0
res = {"seed_s": round(seed_s, 2), "queries": []}
for q in json.load(open(queries)):
    t0 = time.perf_counter()
    ids = [c.id for c in s.recall(q["q"], project=q["project"], limit=q["limit"], ground=False)]
    res["queries"].append({**q, "candidates": "none", "ids": ids, "client_ms": (time.perf_counter() - t0) * 1000})
json.dump(res, open(out, "w"), ensure_ascii=False)
ms = sorted(x["client_ms"] for x in res["queries"])
print(kind, "seed", res["seed_s"], "s; p50 %.1f p95 %.1f ms" % (ms[len(ms) // 2], ms[int(len(ms) * .95)]), file=sys.stderr)
