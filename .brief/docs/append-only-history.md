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

Claims leave recall by terminal retirement (`superseded`, `rejected`) or a temporarily unconfirmed
configured check, not by deletion. Observations are the raw append-only layer under claims.

**Why.** Handles from old context must still `expand`, and provenance must survive consolidation.
An intentional retirement must not be undone by a delayed check. A mistakenly retired assertion is
reasserted as a new linked candidate. A live checked assertion can recover its read eligibility after
a passing check without reviving a retired row.

### Invariant
- No `DELETE` against `memory_claims` or `observations` in kypp code.
- Recall and briefing exclude `superseded` and `rejected`; `expand`/`get` still returns them.
- Consolidation supersedes losers; it does not drop them.
- Rejected and superseded are terminal for every authority; supersession records its successor.
- A check result never retires a claim or revives a retired one. It qualifies a live claim's read
  eligibility independently of its historical acceptance.
