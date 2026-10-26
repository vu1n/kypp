---
id: distill
paths:
  - "kypp/distill.py"
  - "kypp/batch_distill.py"
  - "kypp/episodes.py"
  - "scripts/eval_episodes.py"
  - "eval/episodes/*"
  - "docs/episode-extraction-evaluation.md"
entry:
  - "kypp/distill.py"
  - "kypp/batch_distill.py"
tests:
  - "tests/test_episodes.py"
---
# Distillation

Turns a §0 trace into candidate claims. The heuristic failure-miner is the floor, and an LLM
distiller runs when `KYPP_DISTILL_MODEL` is set (`claude`, `codex` or an ollama model).

## Gotchas
- Without `KYPP_DISTILL_MODEL` you only get heuristic, failure-mined pitfalls.
- If the LLM distiller fails, it falls back to the heuristic distiller rather than raising.
- With `TYPESAFE_API_KEY` or `KYPP_DECIDER_URL` (a local strands-decider) set, and `kypp[s1]`
  installed, a System One gate drops drafts scoring
  p_keep < 0.1. A missing key, failed call or non-probability answer keeps the draft: the gate
  only removes, it never blocks a session.
- A failed rubric becomes a signal-only pitfall: the count of failed criteria and the grader
  scheme. Grader criterion names and feedback stay in the observation layer, never in a claim.
- Shared-scope claims get model names and absolute host paths stripped before storage. A direct
  `claim()` caller is trusted to do this itself; the store doesn't enforce it.
- The LLM distiller sees only the first 40 turns, 400 characters each. `KYPP_EPISODES` (`cues`
  or `clairic`) adds verbatim user corrections and reasons from every turn ahead of them. Episodes
  are §0 event index plus offsets into that event's own text, checked before use; the observation
  stores pointers for the episodes the prompt showed (`episodes.select`), never the quotes. Clairic list answers are phrase fragments with
  meaningless list confidence: code widens each to its sentence. Unreviewed, Clairic quoted a
  pasted "AI agents must ignore prior instructions" line as a correction, so `clairic` always runs
  Clef review, which fails closed. Any error returns no episodes, never a partial or unreviewed
  set. Evaluation and limits: `docs/episode-extraction-evaluation.md`.
