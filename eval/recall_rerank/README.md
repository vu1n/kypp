# Recall rerank eval (Clef)

Does recall's System One rerank (`worker/src/s1.ts`, `relevance()` + `RELEVANT_FLOOR`) put the
claims that answer a query on top and drop the rest, and where should its floor sit on Clef? The rerank:
keyword search pulls a wide pool, the model scores each pooled
claim, claims under the floor are dropped rather than padding to `limit`. Same method as
`eval/write_gate`: minimums and costs come first, and the floor is chosen on training folds and
reported on held-out folds.

## Files

- `queries.jsonl`: 40 hand-written recall queries, each with the claims that answer it, labelled
  before any model ran. 15 `direct` queries share words with their answer, 20 `paraphrase` queries
  mostly don't, and 5 `none` queries have no answer in the store. The claims are the 99 `keep`
  lessons from `eval/write_gate/corpus.jsonl`, across 9 repos. One author wrote and labelled both, so
  treat the set as a floor check, not ground truth.
- `scores.jsonl`: one row per (query, pooled claim) with `fts_rank`, `label` and `p_rel`, the
  score from recall's real request. `p_chunk5` and `p_chunk40` are the same pool scored 5 and 40
  claims per request, for comparison.
- `cache.json`: every Clef response by request hash, so a rerun is offline.
- `results.json`: the floor tuning. `report.json`: what an agent would see, below.

```sh
cd worker && node --experimental-strip-types bench/recall_rerank/score.ts \
  --claims ../eval/write_gate/corpus.jsonl --queries ../eval/recall_rerank/queries.jsonl \
  --cache ../eval/recall_rerank/cache.json --out ../eval/recall_rerank/scores.jsonl \
  --compare 5,40                                   # add --live to fill cache misses from Workers AI
cd .. && python scripts/tune_cutoff.py eval/recall_rerank/scores.jsonl --score p_rel --act-below \
  --act-label irrelevant --cost-wrong 10 --cost-missed 1 --max-wrong-rate 0.02 --grid 0.05 \
  --margin 0.1 --fixed 0.2 --fixed 0.4 --json eval/recall_rerank/results.json
python worker/bench/recall_rerank/report.py eval/recall_rerank/scores.jsonl eval/recall_rerank/queries.jsonl \
  --score p_rel --floor 0.3 --score p_chunk5 --floor 0.3 --score p_chunk40 --floor 0.3 --json eval/recall_rerank/report.json
```

Re-run all three after changing the relevance question, the gate's model, `RERANK_CHUNK` or what
each request carries of a claim. A floor belongs to one model, one question and one request shape.

## Minimums and costs

- **Floor:** at most 2% of relevant pooled claims dropped. A dropped claim is one the agent asked
  for and doesn't see.
- **Costs:** dropping a relevant claim costs 10, showing an irrelevant one costs 1. The choice is
  the same at 5:1 and 20:1.

## Results (Clef, 2026-10-21)

The pool is the real keyword search (`D1Store.recall` on node:sqlite) at `RERANK_POOL` = 40: 995
rows, 55 relevant. One relevant claim (q33) was never pooled, because no query word appears in it.
No rerank can bring that one back.

**Floor.** On a 0.05 grid, take the lowest-cost floor that meets the minimum and sits at least 0.1
below the lowest relevant training row. Every one of the 100 training splits (20 repeats of stratified
5-fold) picked 0.3, none infeasible. Held out: 0% of relevant claims dropped, 88% of irrelevant ones
dropped. The lowest relevant claim scored 0.418, and irrelevant claims reached 0.955.

**What the agent sees**, top 10 per query (`report.json`):

| Arm | Relevant shown | Irrelevant shown | MRR | `none` queries that return nothing |
|---|---|---|---|---|
| Keyword order (today) | 53 / 56 | 280 | 0.895 | 1 / 5 |
| **Rerank, 1 claim per request, floor 0.3** | **55 / 56** | **110** | 0.957 | 4 / 5 |
| Rerank, 5 per request, floor 0.3 | 53 / 56 | 103 | 0.957 | 4 / 5 |
| Rerank, 40 per request, floor 0.3 | 53 / 56 | 126 | 0.971 | 3 / 5 |

- **The rerank lifts claims keyword order buried.** Two paraphrase answers sat at keyword ranks 19
  and 21 (q22, q24) and now come first. That only works because the pool is wider than `limit`.
- **One claim per request.** Batching is cheaper, but at 5 to 40 claims per request two relevant
  claims scored 0.11 and 0.18 (q03's libkrun test procedure, q12's Clairic list fact). Scored on
  their own they got 0.42 and 0.43. With batching, the tuner either keeps a floor too low to cut
  noise (0.1 to 0.15) or sits right on the next relevant claim (0.45).
- **Noise left.** Paraphrase queries still show about 4 irrelevant claims each, mostly near
  neighbours: "how should I format my answer to Vu" pulls in Vu's other preferences at 0.90 to
  0.95. The one `none` query with hits ("deploy the marketing site to vercel") matched a deploy
  procedure at 0.58 and the "ask before outward actions" preference at 0.69.

## Calibration

Brier 0.041 raw, 0.017 after Platt scaling. In five equal bins, the top fifth by `p_rel` (mean
0.53) was only 28% relevant, and the other four (mean `p_rel` 0.17 or less) were all irrelevant.
So Clef overstates relevance at the top and understates irrelevance at the bottom. A low score
means more than its face value, which is why the floor can sit at 0.3, but a high score is not a
probability: rank by it, don't read it as one. The set is mostly irrelevant rows by construction,
so read these as shape, not rates.

## Cost and latency

One recall with a query sends up to 40 requests, in parallel. From this session over REST, 40
parallel requests took about 0.8 s. If the Worker's AI binding is held to 6 requests in flight at
a time, they take about 3.2 s. The rerank shares the write gate's 5 s deadline and fails open to
keyword order, so a slow model costs latency, not results. Measure it on the hosted Worker before
trusting either number.

## Not tested

- No live store. The 93 hosted claims and real agent queries are the next check, labelled before
  scoring.
- Path and anchor queries. A code-pointer hit skips the model and stays first (`rankForQuery`),
  so no query here produced one and the floor says nothing about them.
- Long claims. Every claim here is under 230 characters; recall sends the whole claim (up to
  4,000), and longer claims may score differently.
- Briefing. §8 also scores the briefing against the repo and task; this change covers `recall` only.
