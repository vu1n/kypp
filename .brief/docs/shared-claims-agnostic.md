---
id: shared-claims-agnostic
project: kypp
type: decision
status: active
title: "Shared claims are model-agnostic and machine-agnostic"
related_code:
  - "kypp/distill.py"
---

<!-- brief:anchor agnostic-shared-claims -->
## Strip model names and host paths from shared claims

Distilled claims in shared scopes have model names and absolute host paths redacted before they are stored. The LLM distiller is also prompted for model-agnostic content.

**Why.** Memory is shared across models and machines. Model-entangled memory transfers poorly to other models (MemCollab, arXiv 2603.23234), and local paths leak machine layout across the swarm.

### Invariant
- The distill path applies `_model_agnostic` and `_path_agnostic` to shared-scope claims.
- A direct `claim()` caller is trusted to keep content agnostic; the store does not enforce it.
