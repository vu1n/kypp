---
id: append-only-history
project: kypp
type: decision
status: active
title: "Memory is append-only: claims and observations are never deleted"
related_code:
  - "kypp/store.py"
  - "kypp/arbiter.py"
---

<!-- brief:anchor never-delete -->
## Change a claim's status; never delete a row

Claims leave recall by changing `status` (superseded, rejected), not by deletion. Observations are the raw append-only layer under claims.

**Why.** Handles from old context must still `expand`, provenance must survive consolidation, and a wrongly-rejected claim must be able to revive (for example when its `verify` check passes again).

### Invariant
- No `DELETE` against `memory_claims` or `observations` in kypp code.
- Recall and briefing exclude `superseded` and `rejected`; `expand`/`get` still returns them.
- Consolidation supersedes losers; it does not drop them.
