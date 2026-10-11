---
id: authority-order
project: kypp
type: decision
status: active
signoff: required
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
- Only the operator's `kypp correct` (`shell.correct_main`) writes `authority=human`, and it supersedes the subject's other live claims.
- The one other writer is the hosted Worker's `POST /sync` (amended 2026-10-11, ratified by Vu). The org's CI calls it after `brief ratify` publishes a revision, authenticated by `KYPP_SYNC_TOKEN` (a Worker secret apart from agent OAuth grants and `KYPP_API_TOKENS`; unset means the route answers 404; never reachable through `/mcp`). It upserts an accepted org claim holding the pinned `doc://` ref and the one-line rule. The authority comes from the revision's archive stamp, never from the caller: a person-ratified revision lands at `human`, a model-ratified one (`ratified_via: model`) at `verified`. Every sync is logged in `defrag_log` with the ref and the stamp. No MCP tool passes `authority="human"` or `"verified"`: on the agent surface, "a human told me" is the agent's word, and one confused or prompt-injected agent must not be able to override verified facts across the swarm. MCP `correct` lands accepted at agent authority and top confidence instead.
- Only `MemoryStore.mark_verified` (the `kypp verify` path) sets `authority=verified`.
