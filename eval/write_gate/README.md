# Write gate eval (Clef)

Does the System One write gate (`worker/src/s1.ts`, `judge()` + `DROP_BELOW`) drop junk drafts
without dropping real lessons, and where should its cutoff sit on Clef? Method: Husain and Saha,
[How To Use Decision Models for Evals](https://hamel.dev/blog/posts/decision-model-evals/)
(2026-10-20). Minimums and costs come first, the cutoff is chosen on training folds and reported on
held-out folds, and calibration is checked before reading `p_keep` as a probability.

## Files

- `corpus.jsonl` — 198 hand-written drafts in the shape agents send to `claim`: 99 `keep` (pitfall,
  procedure, preference, decision, fact, artifact, hypothesis) and 99 `drop` (status, play-by-play,
  a fact the code states, one-run detail, chit-chat, and `hard`: status or one-off detail dressed up
  as a lesson). Labels follow the keep question's own criteria and §2 of the `memory-scope-decay`
  decision. One author wrote and labeled them, so treat the set as a floor check, not ground truth.
- `scores.jsonl` — `id, label, kind, model, p_keep, p_control` per draft, from the gate's real
  request (all four questions in one call, `state = {subject, content, project}`).
- `cache.json` — every Clef response by request hash, so a rerun is offline.
- `results.json` — the tuning report below.

```sh
cd worker && node --experimental-strip-types bench/write_gate/score.ts \
  --corpus ../eval/write_gate/corpus.jsonl --cache ../eval/write_gate/cache.json \
  --out ../eval/write_gate/scores.jsonl            # add --live to fill cache misses from Workers AI
cd .. && python scripts/tune_cutoff.py eval/write_gate/scores.jsonl --score p_keep --act-below \
  --act-label drop --cost-wrong 10 --cost-missed 1 --max-wrong-rate 0.02 \
  --fixed 0.1 --fixed 0.2 --fixed 0.25 --fixed 0.3 --json eval/write_gate/results.json
```

Re-run both after changing the keep question's wording or the gate's model: a cutoff belongs to
one model and one question.

## Minimums and costs

- **Floor:** at most 2% of real lessons dropped. A dropped lesson is gone (the agent gets "Not
  stored"), and the next agent pays to rediscover it.
- **Costs:** a dropped lesson costs 10, stored junk costs 1. Junk lands as a candidate that is never
  accepted without a second session and decays out, so it costs briefing noise, not correctness.
  The choice below is the same at 5:1 and 20:1.

## Results (Clef, 2026-10-21)

Held out, 20 repeats of stratified 5-fold (each draft scored 20 times as held-out):

| Cutoff | Lessons dropped | Junk dropped | Cost per 100 |
|---|---|---|---|
| 0.1 (old) | 0% | 46% | 26.8 |
| 0.2 | 0% | 78% | 11.1 |
| **0.25 (new)** | **0%** | **82%** | **9.1** |
| 0.3 | 0% | 84% | 8.1 |
| CV-tuned (mean 0.355) | 1.2% | 89% | 11.7 |

- **0.1 only catches status.** It dropped 19 of 25 status drafts but 1 of 20 code-stated facts,
  7 of 15 play-by-plays and 6 of 15 one-run details. Kept junk scored up to 0.43 for code-stated
  facts and 0.26 for play-by-plays.
- **The cost-optimal cutoff hugs the lowest lesson.** Every training fold picked ~0.35, just under
  the lowest synthetic lesson (0.355), and lost 1.2% of lessons held out. On the live store it would
  be worse: the lowest live lessons score 0.32 (below).
- **Live check (not committed).** The hosted store's 93 claims were re-scored the same way. They are
  all past the old 0.1 gate, so this checks only the lesson side. 91 read as lessons; the lowest
  scored 0.321 and 0.323. The other 2 are account-investigation logs (0.13, 0.19). At 0.25 no live
  lesson is dropped and both logs are. At 0.35 two live lessons (2.2%) would be, which breaks the
  floor. Re-scores matched the scores stored at write time (mean difference 0.015), and Clef gave
  identical scores on a repeat run.
- **0.25 it is:** the floor holds on both sets with ~0.07 margin under the lowest live lesson, and
  it catches 82% of junk. Zero lessons lost in 190 still allows up to about 1.6% (rule of three).

## Calibration

Brier 0.056 raw, 0.038 after Platt scaling (fit on training folds). Clef is under-confident at both
ends. In five equal bins, the two lowest averaged `p_keep` 0.06 and 0.15 and were all junk; the
highest averaged 0.89 and was all lessons. So a low `p_keep` means more than its face value, which
is why the cutoff can rise well above 0.1. Platt changes the scale, not the ranking, so the gate keeps
the raw score with a re-tuned cutoff. The set
is half junk by construction and the real share isn't known, so read these as shape, not rates.

## What a cutoff can't fix

Some junk scores like a lesson: "Hit a pitfall today: forgot to save the file" (0.84), "Anvil
crashed once" (0.67), "rate limited once" (0.57), "for this task Vu wants the summary in the
thread" (0.54). Clef reads the words "pitfall" and "preference" more than the one-off nature. A
narrower question per failure mode (one-off vs durable) is the fix, and it would need its own
re-tune. Python's `distill.GatedDistiller` still drops below 0.1 with a different question and
model (Jev or a local decider) and was not part of this eval.
