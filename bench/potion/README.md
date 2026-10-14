# Potion static embeddings for recall

2026-10-14. Does a model2vec "potion" model (a token lookup table, no transformer) earn the cosine
slot in recall? Measured on claim-shaped rows, against today's local `LIKE` recall and FTS5 BM25.

## Result

| Method | subject R@10 / MRR | identifier R@10 / MRR | paraphrase R@10 / MRR |
|---|---|---|---|
| `LIKE` (local recall today) | 0.05 / 0.01 | 0.70 / 0.64 | 0.02 / 0.01 |
| BM25 (FTS5, OR of terms) | 0.97 / 0.86 | 0.96 / 0.87 | 0.63 / 0.45 |
| cosine only, potion-base-8M | 0.62 / 0.43 | 0.45 / 0.27 | 0.42 / 0.28 |
| cosine only, potion-retrieval-32M | 0.66 / 0.47 | 0.50 / 0.28 | 0.42 / 0.26 |
| cosine only, potion-code-16M-v2 | 0.67 / 0.48 | 0.47 / 0.29 | 0.60 / 0.39 |
| BM25 + 20% cosine, potion-code-16M-v2 | 0.95 / 0.84 | 0.95 / 0.86 | 0.68 / 0.50 |
| BM25 + 30% cosine, potion-code-16M-v2 | 0.94 / 0.83 | 0.93 / 0.86 | 0.70 / 0.52 |
| BM25 + 50% cosine, potion-code-16M-v2 | 0.92 / 0.75 | 0.93 / 0.83 | 0.70 / 0.51 |
| RRF (k=60), potion-code-16M-v2 | 0.85 / 0.66 | 0.90 / 0.77 | 0.68 / 0.49 |
| BM25 + 75% cosine (sgrep's mix), potion-code-16M-v2 | 0.81 / 0.59 | 0.80 / 0.48 | 0.70 / 0.46 |

Paired MRR difference against BM25, 5,000 bootstrap resamples over queries:

| Mix | paraphrase | subject | identifier |
|---|---|---|---|
| BM25 + 20% cosine, code-16M | +0.050 [+0.020, +0.087], 12 better / 1 worse | −0.022 [−0.038, −0.008] | −0.008 [−0.031, +0.014] |
| BM25 + 30% cosine, code-16M | +0.074 [+0.035, +0.119], 14 better / 2 worse | −0.027 [−0.047, −0.007] | −0.010 [−0.033, +0.012] |
| BM25 + 50% cosine, code-16M | +0.063 [+0.001, +0.128] | −0.109 [−0.145, −0.074] | −0.039 [−0.073, −0.006] |

What it says:
- **Ranked keyword search is the big win, not embeddings.** `LIKE` is an unranked OR filter, so
  among matches the order is confidence then recency and the target is almost never in the top
  10. BM25 alone fixes that.
- **Cosine alone loses to BM25 everywhere**, including on paraphrases. Wiring potion in through the
  current `embed` seam (pure cosine order) is still far better than `LIKE`, but it is not where
  recall should end up.
- **A light cosine term on top of BM25 helps paraphrases** by about 5–7 MRR points and costs about
  2 points on exact subject lookups. The weight was picked on these same queries, so treat 20–30%
  as a starting point, not a tuned constant. Heavier mixes (sgrep's 75%) hurt exact lookups badly.
- **potion-code-16M-v2 is the model to use.** It is the only one that helps paraphrases, and it is
  smaller (32 MB, float16, 256 dims) than retrieval-32M (125 MB, 512 dims).

## Cost (this container, CPython 3.13)

| | |
|---|---|
| Embed one claim, pure Python | 3–6 ms |
| Embed one query | 0.2–0.3 ms |
| Process cold start: import + load + first embed | 80–100 ms (mostly parsing tokenizer.json) |
| Store recall, 1,205 claims, tursodb vector scan | 43 ms median (LIKE: 21 ms) |
| Download, once | 33 MB, pinned revision, SHA-256 checked |

## Data and method

- **Corpus:** 1,205 Markdown sections from vu1n/dev, brief, kypp and pillbox plus the project's
  research and review notes, as claims (subject = heading, content = first 800 chars of the body).
  `corpus.py` rebuilds it; ids hash the body, so other trees give other ids.
- **subject** (200): the query is the claim's own heading. Exact recall of a known subject.
- **identifier** (80): a camelCase, snake_case or dotted token found in only one claim.
- **paraphrase** (60, `paraphrase.json`): written by a separate model pass that saw each target
  claim and was told to describe the task without its distinctive words or identifiers. These are
  the "agent knows the problem, not the wording" case.
- Hybrids take the top 200 of each list, min-max scale each, and add them by weight (as sgrep
  does). RRF uses k=60. Cosine is computed in numpy here; the store does it in tursodb.

Single corpus, one person's repos, 60 paraphrase queries: enough to rank the options, not to
claim a general number.

```sh
uv run python -c "from kypp.potion import fetch; print(fetch('potion-code-16M-v2'))"   # model dir
python3 -I bench/potion/corpus.py /tmp/corpus.json ../dev ../brief ../kypp ../pillbox
python3 -I bench/potion/queries.py /tmp/corpus.json /tmp/queries.json
uv run --with numpy python bench/potion/eval.py /tmp/corpus.json /tmp/queries.json ~/.cache/kypp/models potion-code-16M-v2@e9d2a44ca6a0
```
