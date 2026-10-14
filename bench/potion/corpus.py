"""Markdown sections → claim-shaped rows: subject = heading, content = body (<= 800 chars).
usage: corpus.py OUT.json ROOT [ROOT...]   (ids hash the body, so the same trees give the same ids)"""
import glob, json, os, re, random, sys, hashlib
roots = sys.argv[2:]
rows, seen = [], set()
for root in roots:
    for f in sorted(glob.glob(root + "/**/*.md", recursive=True)):
        if "/node_modules/" in f or "/.git/" in f: continue
        try: text = open(f, encoding="utf-8").read()
        except Exception: continue
        parts = re.split(r"(?m)^#{1,4} +(.+)$", text)
        for h, body in zip(parts[1::2], parts[2::2]):
            body = re.sub(r"\s+", " ", body).strip()
            h = h.strip()
            if len(body) < 120 or len(h) < 8: continue
            key = hashlib.sha1(body[:400].encode()).hexdigest()
            if key in seen: continue
            seen.add(key)
            rows.append({"id": key[:16], "subject": h, "content": body[:800], "path": os.path.relpath(f, os.path.dirname(root))})
random.seed(7)
random.shuffle(rows)
json.dump(rows, open(sys.argv[1], "w"))
print(len(rows), "rows;", len({r['path'].split('/')[0] for r in rows}), "roots")
