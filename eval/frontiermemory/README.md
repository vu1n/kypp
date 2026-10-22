# FrontierMemory adapter (pilot)

Measured 2026-10-22. Memory generation and retrieval only: no task agent was run, so there is no
solve rate here yet.

[FrontierMemory](https://frontiermemory.com/) (Letta) scores memory by downstream solve rate. A
memory system "dreams" over prior trajectories (labelled pass/fail) for up to 6 hours, then a task
agent tries 15 memory-dependent tasks with that memory, one binary verifier per task. The leaderboard
reports the better of Opus 5.5 and GPT-6 Sol at max effort. The top score is 73.3% (Letta Dream).

## What is public, and what isn't

- 15 task pages and the 219 task-relevant source trajectories (HTML, verifier output included).
  The 446 distractor trajectories are not published.
- The task verifiers mostly are not. Letta repaired the verifiers on 53% of the sourced tasks and
  rewrote the prompts. A full run needs the dataset from Letta (submission is by email).
- One exception is Word2Vec Pipeline (`harbor-index/build-word2vec-pipeline`). The FrontierMemory
  traces show the same six-test Harbor verifier, so that task can be rebuilt here with FrontierMemory's
  instruction.

## How kypp plugs in

| FrontierMemory phase | kypp path |
|---|---|
| source trajectories | `fm_traces.py`: HTML trace → §0 events (`user_message`, `assistant_message`, `tool_call`, `scored` with per-test criteria) |
| dream | `dream.py`: `build_trace` → `LLMDistiller` → `distill_session` → `arbiter.consolidate` (the corroboration gate). kypp's write path is unchanged; only `complete` is swapped for the Anthropic API with a response cache |
| task agent memory | the agent gets kypp over MCP (`briefing`, `recall`, `expand`) and the repo's standard AGENTS.md block |

All 15 tasks share one project (`frontiermemory`), so recall has to pick a task's lessons out of
everyone else's, as it would in a shared store.

## Results

Distiller: Opus 5.5, effort high. 219 traces → 1,042 claims, none failed. 209 API calls,
657k input and 283k output tokens, about $8.30.

| | count |
|---|---|
| claims | 1,042 |
| promoted to accepted by consolidate | 72 (7%) |
| superseded | 102 |
| left as candidates | 868 |

Distillation found the lessons a Word2Vec agent needs. All 9 source runs failed the empty-input
test, and the 32 Word2Vec claims say it repeatedly: `task_func(texts, stopwords=None)` in
`/workspace/solution.py`, the `[\W_]+` cleaning, and "an empty corpus must return an untrained
model, not raise". All 32 are still candidates. Each session named the lesson differently ("Gensim
Word2Vec on an empty corpus", "Word2Vec crashes on empty corpus", …), and consolidation corroborates
exact subjects only. So the surfaces an agent uses by default (`briefing`, and `recall` without
`include_candidates`) show none of it.

Recall, top 10, number of results from the task's own trajectories (`recall_check.py`):

| query | accepted only (default) | with candidates |
|---|---|---|
| task instruction, summed over 15 tasks | 26 / 150 | 75 / 150 |
| Word2Vec | 0 / 10 | 10 / 10 |
| tasks with 0 own results | 8 of 15 | 1 of 15 |

The `briefing` digest gives 10 of its 12 lines to one task (network intrusion), because that task
has the most accepted claims.

## Reproduce

```sh
python eval/frontiermemory/fm_traces.py fetch  --cache /tmp/fm/cache
python eval/frontiermemory/fm_traces.py events --cache /tmp/fm/cache --out /tmp/fm/events
tumbler run -- uv run --with anthropic python eval/frontiermemory/dream.py \
  --logs /tmp/fm/events --db /tmp/fm/kypp.db --cache /tmp/fm/dream_cache.json --live
uv run python eval/frontiermemory/recall_check.py --db /tmp/fm/kypp.db --logs /tmp/fm/events
```

The trace cache and the response cache hold Letta's data, so they stay out of git.

## Not done: the task-agent run

Harbor 0.24 and Docker run here, and `harbor download harbor-index/build-word2vec-pipeline` works.
The image pulls from `mirror.gcr.io`, because Docker Hub rate-limits this egress. What's blocked is
the agent's network. The Claude Code agent runs inside the task container and needs the API. The
sandbox refused both ways to give it the API: putting the container on the host network to reach the
egress proxy, and a host-side agent that runs commands in a network-less container. On a machine with
normal Docker egress, run it like this:

```sh
# fm-word2vec = the Harbor task with instruction.md replaced by FrontierMemory's prompt
harbor run -p fm-word2vec -a claude-code -m anthropic/claude-opus-5-5 --ak reasoning_effort=max -k 5
harbor run -p fm-word2vec -a claude-code -m anthropic/claude-opus-5-5 --ak reasoning_effort=max -k 5 \
  --mcp-config kypp.mcp.json --ak append_system_prompt="$(cat agents-kypp-block.md)"
```

Run three arms (none, kypp as shipped, kypp with candidates visible) at 5 attempts each. The
leaderboard's Opus 5.5 average is $2.80 per task, so 15 attempts cost at most about $40. Word2Vec is
one of the smaller tasks.
