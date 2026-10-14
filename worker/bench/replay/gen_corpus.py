"""Deterministic synthetic corpus for the local/hosted replay test.

Claims are sentences cut from the repos' own Markdown (real identifiers, paths, prose), spread over
projects, scopes and statuses, with a slice of non-ASCII text. Writes corpus.jsonl and queries.json.
usage: python -I gen_corpus.py <markdown-root>... --n 20000 --q 200
"""
import argparse, json, pathlib, random, re

ap = argparse.ArgumentParser()
ap.add_argument("roots", nargs="+")
ap.add_argument("--n", type=int, default=20000)
ap.add_argument("--q", type=int, default=200)
ap.add_argument("--out", default=".")
a = ap.parse_args()
rnd = random.Random(20261014)

sents = []
for root in a.roots:
    for p in sorted(pathlib.Path(root).rglob("*.md")):
        if "node_modules" in p.parts:
            continue
        for s in re.split(r"(?<=[.!?])\s+|\n\n+", p.read_text(errors="ignore")):
            s = " ".join(s.split())
            if 40 <= len(s) <= 400 and not s.startswith(("|", "```", "<!--")):
                sents.append(s)
sents = sorted(set(sents))
rnd.shuffle(sents)

# Non-ASCII probes: accents, sharp s, dotted I, CJK, emoji. Each appears in some claims.
INTL = ["café rotation", "naïve retry loop", "Straße routing", "İstanbul region", "日本語 tokenizer",
        "résumé parser", "crème brûlée cache", "emoji 🚀 deploy", "Ångström units", "façade pattern"]
PROJECTS = ["kypp", "pillbox", "brief", "dev"] + [f"proj{i:02d}" for i in range(16)]
TYPES = ["fact", "pitfall", "procedure", "decision", "preference"]

def status():
    r = rnd.random()
    return "accepted" if r < .5 else "candidate" if r < .8 else "rejected" if r < .9 else "superseded"

claims = []
for i in range(a.n):
    body = " ".join(rnd.sample(sents, rnd.randint(2, 6)))[:1800]
    if rnd.random() < .03:
        body = f"{rnd.choice(INTL)}: {body}"
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", body)
    subject = " ".join(words[:3]).lower() or f"claim {i}"
    scope = "global" if rnd.random() < .05 else "project"
    claims.append({
        "id": f"{i:032x}", "type": rnd.choice(TYPES), "subject": subject, "content": body,
        "scope": scope, "project": None if scope == "global" else rnd.choice(PROJECTS),
        "status": status(), "confidence": round(rnd.choice([.5, .6, .7, .7, .8, .9]), 2),
        # Coarse timestamps on purpose: many ties, so ordering must not lean on them.
        "ts": f"2026-10-{1 + i % 13:02d}T00:00:00.000Z",
    })

queries = []
for _ in range(a.q):
    c = rnd.choice(claims)
    toks = re.findall(r"\w+", c["content"])
    kind = rnd.random()
    if kind < .6:
        q = " ".join(rnd.sample(toks, min(len(toks), rnd.randint(1, 4))))
    elif kind < .8:
        idents = [t for t in toks if "_" in t] or toks
        q = rnd.choice(idents)
    else:
        q = rnd.choice(INTL).split(":")[0]
        if rnd.random() < .5:  # the accent-stripped spelling a user would type
            q = q.replace("é", "e").replace("ï", "i").replace("ß", "ss").replace("ç", "c").replace("Å", "A")
    queries.append({"q": q, "project": rnd.choice(PROJECTS[:8]), "limit": 10})

out = pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=True)
with open(out / "corpus.jsonl", "w") as f:
    for c in claims:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
json.dump(queries, open(out / "queries.json", "w"), ensure_ascii=False, indent=0)
print(len(sents), "sentences;", len(claims), "claims;",
      round(sum(len(c["content"]) for c in claims) / 1e6, 1), "MB content;", len(queries), "queries")
