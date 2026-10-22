# Agreement gate eval (Clef)

Measured 2026-10-22 on claims distilled from the FrontierMemory traces (`eval/frontiermemory/`).
There's no task agent in this eval. One labeller (Opus 5.5) made the gold, and I spot-checked 14
pairs by hand. Treat it as a dev set, not ground truth.

## Question

The corroboration gate promotes a candidate once two sessions agree, and today it groups on the
exact subject. LLM distillation names each lesson its own way, so on the FrontierMemory store 7% of
claims were ever promoted. Word2Vec's 32 claims stayed candidates, though 11 of them state the same
empty-corpus pitfall. `memory-scope-decay` (active) allows automatic acceptance on "an affirmative
agreement judgment plus distinct non-echo recurrence". This eval tests Clef as that judgment, with a
closed set of tags narrowing which claims get compared.

## Method

1. **Pairs** (`pairs.py build`). This gives 600 pairs of claims from different sessions that share a
   tag (a task, here). Per tag, it takes the 25 most word-similar pairs plus 15 at random.
2. **Labels** (`pairs.py label`). Opus 5.5 labels each pair same, different or conflict. The rubric
   that counts is `agree`: same main point, extra detail allowed. A first rubric, `redundant`
   ("one makes the other unnecessary"), was stricter than corroboration needs, so it is kept only
   for comparison.
3. **Scores** (`pairs.py score`). Each pair is scored by Clef's P(agree) through the production
   question (`s1.agree_question`), with the state `arbiter` sends. That's 600 Workers AI calls.
4. **Cutoff** (`scripts/tune_cutoff.py`, act when p ≥ t). A false agreement costs 10 and a missed one
   costs 1, at most 2% wrong, with repeated stratified 5-fold. `leave_task_out.py` then tunes on 14
   tags and scores the 15th.
5. **Tagging** (`tagging.py`). Clef picks each claim's tag from the 15 task summaries, choosing one
   of the 15 per claim in calls of 16 claims.
6. **Before and after** (`gate_before_after.py`). This reruns `consolidate` on a copy of the store
   with `tag_of` set to Clef's tags and `pair_judge` set to Clef, then reruns
   `eval/frontiermemory/recall_check.py`.

## Results

Pair judge, `agree` rubric, 219 same / 379 not (2 pairs came back unlabelled):

| cutoff | held-out precision | catch | wrong rate |
|---|---|---|---|
| tuned (0.89–0.91) | 0.959 | 0.761 | 1.9% |
| 0.9, leave one tag out | 0.961 | 0.781 | 1.8% |
| 0.5 | 0.887 | 0.964 | 7.1% |

Brier is 0.045 raw, and it is close to calibrated: the top two fifths of scores predict 0.84 and
0.96 and observe 0.81 and 0.96. Errors at 0.9 are mostly a method versus its computed result on
the same quantity. On the stricter `redundant` labels the same scores give 0.91 precision and 0.44
catch at the cutoff tuned for them.

Tagging accuracy is 739/940 (79%). Every miss falls in one of three groups of sibling tasks built
from the same upstream repo or company (awilix ×3, compositional-changelog ×2, Brightpath ×2).
Those tasks genuinely share lessons. The other 8 tags score 97–100%.

Gate, before and after (`gate_results.json`, `recall_after.json`):

| | before | after |
|---|---|---|
| accepted | 72 | 215 |
| superseded | 102 | 386 |
| own-task results in top 10, default recall, 15 task prompts | 26/150 | 55/150 |
| tasks with none | 8 | 4 |
| same, with candidates included | 75/150 | 70/150 |
| Word2Vec accepted / default recall own | 0 / 0 of 10 | 4 / 3 of 10 |
| labelled pairs inside a merged cluster that are not the same | — | 5/118 (4.2%) |

The four accepted Word2Vec claims are the interface convention, the empty-corpus crash, the
`min_count` pitfall, and "test edge cases". Merging restatements costs a little recall when
candidates are shown (75 to 70), because superseded duplicates no longer fill the top 10.

Two traps the eval caught:
- **Transitive chaining.** Union-find over agreeing pairs chained different lessons into clusters
  of up to 25 (`labelled not-same` 11/163). The gate now merges only the survivor's direct matches
  and peels clusters repeatedly.
- **Rubric.** The first labels asked for redundancy, not agreement, and made Clef look worse than
  it is on the gate's actual question.

## Follow-up: where the rest of the loss is

- **Hosted rerank.** `eval/frontiermemory/rerank_check.py` reproduces the hosted Worker's recall:
  a keyword pool of 40, Clef relevance per claim, floor 0.3. On top of the gate, own-task results go
  from 55 to 62 of 150 and tasks with none from 4 to 2. Without the gate the same rerank gives 33,
  so the gate and the rerank add up.
- **The two tasks still at zero** (awilix explicit and implicit) have a generic prompt ("you're in
  `/app`, ask the maintainer, save a plan"). Their lessons are about the maintainer's process, and
  recall returns the same lessons filed under sibling tasks ("plan-only step must stop after saving
  the plan"). An agent would recall again after reading the repo, so a prompt-only query is the
  wrong probe for these. Chasing them would mean tuning to the benchmark.
- **Neighbours per claim.** 4, 8 and 16 neighbours give 61, 55 and 56 own-task results. That's
  within noise on 150, so the default stays at 8. 4 would halve the Clef calls (2,447 versus 4,686),
  and that decision belongs on a held-out store.

## Not done

- **No held-out repo yet.** These claims all come from one benchmark. Rerun steps 1–4 on the
  hosted store's own claims before trusting 0.9 there.
- **No production tag source.** A repo's tags would be its Brief feature map, fed through the same
  choice question. `consolidate(tag_of=…, pair_judge=…)` is a seam, so no CLI or cron passes it
  yet.
- **No hosted port.** `worker/src/memory.ts` doesn't have the phase. Port it before enabling the
  phase anywhere, or local and hosted promote differently.
- **Briefing is still dominated by one task** (11 of 12 lines). That's a separate ranking problem.
- **Clef cost.** 4,686 pair calls for 940 claims (8 neighbours each) at the write-gate eval's rate
  of about $0.0002 per call, so about $1 per full pass. An incremental pass would only judge new
  claims.

## Reproduce

```sh
uv run python eval/agreement/pairs.py build --db /tmp/fm/kypp.db
tumbler run -- uv run --with anthropic python eval/agreement/pairs.py label --rubric agree
tumbler run -- uv run python eval/agreement/pairs.py score --rubric agree
python scripts/tune_cutoff.py eval/agreement/scores_agree.jsonl --score p_agree --act-above \
  --act-label same --cost-wrong 10 --cost-missed 1 --max-wrong-rate 0.02 --grid 0.01 --fixed 0.9 \
  --json eval/agreement/results_agree.json
python eval/agreement/leave_task_out.py eval/agreement/scores_agree.jsonl
tumbler run -- uv run python eval/agreement/tagging.py --db /tmp/fm/kypp.db --site /tmp/fm/cache --tags-out /tmp/fm/tags.json
tumbler run -- uv run python eval/agreement/gate_before_after.py --db /tmp/fm/kypp.db --tags /tmp/fm/tags.json --after /tmp/fm/kypp-after.db
uv run python eval/frontiermemory/recall_check.py --db /tmp/fm/kypp-after.db --logs /tmp/fm/events
```

`pairs.jsonl` and the response caches hold claim text distilled from Letta's traces, so they stay
out of git. The committed `scores_*.jsonl` carry ids, labels and scores only.
