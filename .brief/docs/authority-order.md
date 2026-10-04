---
id: authority-order
project: kypp
type: decision
status: active
title: "Authority outranks confidence and corroboration: agent < verified < human"
related_code:
  - "kypp/vocab.py"
  - "kypp/arbiter.py"
---

<!-- brief:anchor authority-dominates -->
## Rank survivors by authority first

When claims on the same subject compete, the survivor is chosen by authority first (`agent` < `verified` < `human`), then status, confidence, evidence and recency. A human correction beats any number of agreeing agent claims, whatever its confidence number.

**Why.** Authority is the variance-free signal. Agents can be confidently and repeatedly wrong together, so a human answer or a passing `verify` check must win.

### Invariant
- `AUTHORITY_RANK` in `vocab.py` is the single ordering, and the arbiter's survivor key uses it first.
- `correct` writes `authority=human` and supersedes the subject's other live claims.
- Only `MemoryStore.mark_verified` (the `kypp verify` path) sets `authority=verified`.
