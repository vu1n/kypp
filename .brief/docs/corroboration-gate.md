---
id: corroboration-gate
project: kypp
type: decision
status: active
title: "Agent claims start as candidates and are accepted only by independent corroboration"
related_code:
  - "kypp/arbiter.py"
  - "kypp/view.py"
  - "kypp/store.py"
---

<!-- brief:anchor swarm-truth-gate -->
## Promote a candidate only when distinct sessions agree

A plain agent claim lands as a `candidate`. Candidates are invisible to `briefing` and default recall. A candidate is promoted to `accepted` only when at least K (default 2) distinct claims from distinct sessions agree on the subject. `--accept`, `decide` and human corrections are the explicit exceptions.

**Why.** One session's guess must not become the swarm's truth. A single claim citing many sources is still one opinion.

### Invariant
- A single claim cannot self-corroborate, whatever its `source_ids` count.
- Briefing and default recall return only `accepted` claims unless candidates are explicitly requested.
