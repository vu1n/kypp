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
- `MemoryStore.mark_verified` (the `kypp verify` path) is the only local runtime-verification path that grants verified authority. The hosted Worker's authenticated `POST /sync` is a separate import path for ratified Brief revisions (amended 2026-10-11, ratified by Vu): person-ratified revisions receive human authority and model-ratified revisions receive verified authority, derived from the trusted archive stamp, not caller-selected authority. A model ratification is stamped `ratified_via: model`, so a consumer can tell it apart from a deterministic `verify` result. Neither privileged path is available through MCP. No other writer is authorized.
- The org's CI calls `POST /sync` after `brief ratify` publishes a revision, authenticated by `KYPP_SYNC_TOKEN` (a Worker secret apart from agent OAuth grants and `KYPP_API_TOKENS`; unset means the route answers 404; never reachable through `/mcp`). It upserts an accepted org claim holding the pinned `doc://` ref and the one-line rule. Every sync is logged in `defrag_log` with the ref and the stamp. No MCP tool passes `authority="human"` or `"verified"`: on the agent surface, "a human told me" is the agent's word, and one confused or prompt-injected agent must not be able to override verified facts across the swarm. MCP `correct` lands accepted at agent authority and top confidence instead.
