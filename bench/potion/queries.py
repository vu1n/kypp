"""usage: queries.py corpus.json OUT.json
Auto query sets 'subject' (query = heading) and 'identifier' (a code token unique to one claim),
plus the hand-built 'paraphrase' set next to this file."""
import collections, json, os, random, re, sys
rows = json.load(open(sys.argv[1]))
random.seed(3)
subj = [{"id": r["id"], "query": r["subject"]} for r in random.sample(rows, 200)]
tok = re.compile(r"\b(?:[a-z]+[A-Z][A-Za-z0-9]*|[A-Za-z]+_[A-Za-z0-9_]+|[A-Za-z]+\.[a-z_]+\(?|[A-Z][a-z]+[A-Z][A-Za-z]+)\b")
df = collections.Counter()
per = {}
for r in rows:
    toks = set(tok.findall(r["subject"] + " " + r["content"]))
    per[r["id"]] = toks
    df.update(toks)
cands = [(r["id"], t) for r in rows for t in sorted(per[r["id"]]) if df[t] == 1 and len(t) >= 6]
random.shuffle(cands)
ident, used = [], set()
for i, t in cands:
    if i in used: continue
    used.add(i); ident.append({"id": i, "query": t})
    if len(ident) == 80: break
para = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "paraphrase.json")))
json.dump({"subject": subj, "identifier": ident, "paraphrase": para}, open(sys.argv[2], "w"), indent=1)
print(len(subj), len(ident), ident[:5])
