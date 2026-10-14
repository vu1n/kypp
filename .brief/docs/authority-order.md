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
- The operator's `kypp correct` and `kypp verify` are the only writers of `authority=human` and of runtime-verified `authority=verified`, and `kypp correct` supersedes the subject's other live claims. They run on the operator's machine and write through the hosted Worker's authenticated `POST /v1/operator`, using `KYPP_OPERATOR_TOKEN` (a Worker secret apart from agent OAuth grants, `KYPP_API_TOKENS` and `KYPP_SYNC_TOKEN`; unset means the route answers 404; never reachable through `/mcp`). Every operator write is logged in `defrag_log`. When the CLI targets a local Worker (the documented workaround), the same route and token apply (amended 2026-10-14, ratified by Vu). That route is not built yet, and the contract binds when it lands; until then `shell.correct_main` and `MemoryStore.mark_verified` remain the operator's local writers.
- The hosted Worker's authenticated `POST /sync` is the one other writer of human and verified authority: a separate import path for ratified Brief revisions, not built yet; the contract binds when it lands (amended 2026-10-11, ratified by Vu): person-ratified revisions receive human authority and model-ratified revisions receive verified authority, derived from the trusted archive stamp, not caller-selected authority. A model ratification is stamped `ratified_via: model`, so a consumer can tell it apart from a deterministic `verify` result. None of these privileged paths is available through MCP. No other writer is authorized.
- When `POST /sync` lands, the org's CI calls it after `brief ratify` publishes a revision, authenticated by `KYPP_SYNC_TOKEN` (a Worker secret apart from agent OAuth grants and `KYPP_API_TOKENS`; unset means the route answers 404; never reachable through `/mcp`). It upserts an accepted org claim holding the pinned `doc://` ref and the one-line rule. Every sync is logged in `defrag_log` with the ref and the stamp. No MCP tool passes `authority="human"` or `"verified"`: on the agent surface, "a human told me" is the agent's word, and one confused or prompt-injected agent must not be able to override verified facts across the swarm. MCP `correct` lands accepted at agent authority and top confidence instead.
