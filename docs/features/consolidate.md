---
id: consolidate
paths:
  - "kypp/arbiter.py"
entry:
  - "kypp/arbiter.py"
---
# Consolidation

`kypp consolidate` dedups by subject, and semantically when given `--semantic`, superseding
the weaker claims. It also promotes corroborated candidates.

## Gotchas
- On a small or diverse corpus the corroboration gate (K=2) can promote nothing, leaving the
  briefing empty. That shows up as "memory had no effect", not as an error.
- A candidate written after an accepted claim on the same subject (same authority) is a pending
  update: it is kept, not superseded, and replaces the old answer once it corroborates. Without
  that, accepted-beats-candidate made every update lose to the answer it corrects. Exact-subject
  groups only; the semantic pass doesn't keep updates.
- Corroboration counts a claim's `session:` stamp when it has one, never the `source_ids` an MCP
  caller cited. Those stay as provenance, but a cited `session:` id is stored as `cited:session:…`,
  and an HTTP server with no `KYPP_SESSION` (nothing to stamp) stores every cited id as `cited:…`,
  which never counts. Otherwise one session promotes its own guess with invented ids or
  observations it filed. The hosted Worker stores only the stamp, to the same effect.
- The S1 agreement judge only holds a promotion; `None` (no model, failed call) never blocks one.
  A held group still supersedes its weaker duplicates, so the subject waits for a fresh session.
- The agreement phase (`tag_of` + `pair_judge`, off unless both are passed) is the one place a model
  answer can cause a promotion. It joins two claims only on an affirmative judgment, and only the
  survivor's direct matches merge: union-find over A~B, B~C chained different lessons into clusters
  of 25 on the FrontierMemory store. A shared tag is never agreement by itself. The hosted Worker's
  `memory.ts` does not have this phase yet, so turning it on locally makes local and hosted promote
  differently. Cutoff and its eval: `eval/agreement/`.
