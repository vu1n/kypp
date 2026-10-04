---
id: seed-or-donate
project: kypp
type: decision
status: active
title: "A transcript seeds memory or donates an eval task, never both"
related_code:
  - "kypp/seed.py"
  - "kypp/evaltasks.py"
  - "kypp/transcripts.py"
---

<!-- brief:anchor seed-or-donate-never-both -->
## Keep eval-task sessions out of memory

`kypp seed` skips eval-contaminated sessions (eval task directories, task-shaped prompts), and those sessions are the ones `mine-tasks` may turn into eval tasks.

**Why.** If a session both seeds memory and becomes an eval task, the memory contains the answer to its own benchmark and every lift measurement is invalid.

### Invariant
- `seed` skips sessions that `is_eval_contaminated` flags, unless explicitly overridden.
- Skipped sessions are not marked seeded, so a refined filter can reconsider them.
