"""Recall eval: LIKE (store today), BM25 (FTS5), potion cosine (store with embedder), hybrids.
usage: eval.py corpus.json queries.json MODELS_DIR model[,model...]   (needs numpy: uv run --with numpy)
MODELS_DIR/<model> holds a model's tokenizer.json + model.safetensors (kypp.potion.fetch writes one)."""
import json, os, re, sqlite3, sys, tempfile, time, statistics
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from kypp.store import MemoryStore
from kypp.potion import StaticModel

corpus = json.load(open(sys.argv[1])); qsets = json.load(open(sys.argv[2]))
models_dir, models = sys.argv[3], sys.argv[4].split(",")
ids = [r["id"] for r in corpus]
text = {r["id"]: f"{r['subject']}\n{r['content']}" for r in corpus}
K = 10

def metrics(ranked, target):
    r = ranked[:K]
    return (1.0 if target in r else 0.0), (1.0 / (r.index(target) + 1) if target in r else 0.0)

# --- BM25 via FTS5, OR of terms (hosted-style query), visibility is trivially all rows
fts = sqlite3.connect(":memory:")
fts.execute("CREATE VIRTUAL TABLE f USING fts5(id UNINDEXED, subject, content)")
fts.executemany("INSERT INTO f VALUES (?,?,?)", [(r["id"], r["subject"], r["content"]) for r in corpus])
def bm25(q, n=200):
    terms = re.findall(r"\w+", q)
    if not terms: return []
    m = " OR ".join('"%s"' % t.replace('"', '') for t in terms)
    return fts.execute("SELECT id, -bm25(f) FROM f WHERE f MATCH ? ORDER BY bm25(f) LIMIT ?", (m, n)).fetchall()

# --- LIKE via the real store
def mkstore(embed=None):
    d = tempfile.mkdtemp(); s = MemoryStore(os.path.join(d, "m.db"), embed=embed)
    return s
def fill(s):
    idmap = {}
    for r in corpus:
        cid = s.claim("fact", r["subject"], r["content"], scope="project", project="p", accept=True)
        idmap[cid] = r["id"]
    return idmap

def fuse(lex, cos, w=0.75):
    """sgrep-style: union of top-200s, min-max each, w*cos + (1-w)*bm25."""
    def scale(d):
        if not d: return {}
        lo, hi = min(d.values()), max(d.values())
        return {k: (v - lo) / (hi - lo) if hi > lo else 1.0 for k, v in d.items()}
    L, C = scale(lex), scale(cos)
    keys = set(L) | set(C)
    return sorted(keys, key=lambda k: (-(w * C.get(k, 0) + (1 - w) * L.get(k, 0)), k))
def rrf(*lists, k=60):
    sc = {}
    for lst in lists:
        for i, x in enumerate(lst): sc[x] = sc.get(x, 0) + 1 / (k + i + 1)
    return sorted(sc, key=lambda x: (-sc[x], x))

results = {}
def add(method, qs, ranker):
    for name, qlist in qs.items():
        rs = [metrics(ranker(q["query"]), q["id"]) for q in qlist]
        results.setdefault(method, {})[name] = (np.mean([a for a, _ in rs]), np.mean([b for _, b in rs]))

like = mkstore(); lmap = fill(like)
# LIKE ordering among matches is confidence/recency (all equal) → effectively insertion order.
add("LIKE (today)", qsets, lambda q: [lmap[c.id] for c in like.recall(q, project="p", limit=K, ground=False)])
add("BM25", qsets, lambda q: [i for i, _ in bm25(q)])
timing = {}
for m in models:
    sm = StaticModel(os.path.join(models_dir, m))
    t0 = time.perf_counter(); E = np.array([sm.embed(text[i]) or [0.0] * sm.dim for i in ids], dtype=np.float32); t_doc = (time.perf_counter() - t0) / len(ids)
    def cosr(q, n=200):
        v = sm.embed(q)
        if not v: return []
        s = E @ np.array(v, dtype=np.float32); top = np.argsort(-s)[:n]
        return [(ids[j], float(s[j])) for j in top]
    add(f"cosine {m}", qsets, lambda q: [i for i, _ in cosr(q)])
    add(f"hybrid 75/25 {m}", qsets, lambda q: fuse(dict(bm25(q)), dict(cosr(q))))
    for w in (0.1, 0.2, 0.3, 0.4):
        add(f"hybrid cos{int(w*100)} {m}", qsets, lambda q, w=w: fuse(dict(bm25(q)), dict(cosr(q)), w))
    add(f"hybrid 50/50 {m}", qsets, lambda q: fuse(dict(bm25(q)), dict(cosr(q)), 0.5))
    add(f"RRF {m}", qsets, lambda q: rrf([i for i, _ in bm25(q)], [i for i, _ in cosr(q)]))
    allq = [q["query"] for ql in qsets.values() for q in ql]
    t0 = time.perf_counter()
    for q in allq: sm.embed(q)
    timing[m] = (t_doc * 1000, (time.perf_counter() - t0) / len(allq) * 1000)

names = list(qsets)
print("| method | " + " | ".join(f"{n} R@10 / MRR" for n in names) + " |")
print("|---|" + "---|" * len(names))
for meth, d in results.items():
    print(f"| {meth} | " + " | ".join(f"{d[n][0]:.2f} / {d[n][1]:.2f}" for n in names) + " |")
print("\nembed ms per claim / per query (pure Python):", {m: tuple(round(x, 2) for x in v) for m, v in timing.items()})
print("queries:", {n: len(v) for n, v in qsets.items()}, "corpus:", len(corpus))
