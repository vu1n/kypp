"""Compare replay outputs pairwise against a reference.
usage: python -I compare.py <ref.json> <other.json>... [--mode none|origin]
Per query: identical ordered list, same set but different order, overlap of the top-k sets, and
how often one side returned fewer results.
"""
import json, sys
args = [a for a in sys.argv[1:] if not a.startswith("--")]
mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "none"
args = [a for a in args if a != mode]
load = lambda p: {(q["q"], q["project"]): q["ids"] for q in json.load(open(p))["queries"] if q.get("candidates", "none") == mode}
ref = load(args[0])
print(f"reference {args[0]} ({mode}); {len(ref)} queries; {sum(1 for v in ref.values() if not v)} with no results")
for p in args[1:]:
    o = load(p)
    same = reorder = 0; jac = []; fewer = more = 0; examples = []
    for k, a in ref.items():
        b = o.get(k, [])
        if a == b: same += 1
        elif set(a) == set(b): reorder += 1
        else: examples.append((k, len(a), len(b)))
        u = set(a) | set(b)
        jac.append(len(set(a) & set(b)) / len(u) if u else 1.0)
        fewer += len(b) < len(a); more += len(b) > len(a)
    n = len(ref)
    print(f"  vs {p}: identical {same}/{n}, same set reordered {reorder}, mean top-k overlap {sum(jac)/n:.2f}, "
          f"fewer results {fewer}, more results {more}")
    for k, la, lb in examples[:4]:
        print(f"     e.g. {k[0]!r} in {k[1]}: ref {la} hits, other {lb}")
