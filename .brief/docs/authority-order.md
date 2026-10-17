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
**Activation:** authority ordering and privileged-route boundaries bind throughout rollout. The new
replacement, candidate-correction and transactional rules below bind a store at complete adapter
cutover in `docs/claim-state-machine.md`. Until then, legacy arbitration ranks authority, accepted
status, confidence, evidence and recency, and MCP correct writes accepted agent claims. Merge
ratifies the target contract; the following adapter slice activates it for production stores.

## Rank survivors by authority first

Within one arbitration group `(subject, scope, project, owner when applicable)`, an accepted answer
may be replaced only at equal or higher authority (`agent` < `verified` < `human`). Eligible
replacements at the same authority use explicit transactional commit order, not confidence,
source-count or timestamp tie-breaking. Candidates remain proposals until an authorized acceptance
transition. A human correction beats any number of agreeing agent claims.

**Why.** Authority is the variance-free signal. Agents can be confidently and repeatedly wrong together, so a human answer or a passing `verify` check must win.

### Invariant
- `AUTHORITY_RANK` in `vocab.py` is the runtime ordering; all command adapters use it to constrain
  accepted-answer replacement. Portable reference vectors keep the Worker and Python order aligned.
- Authority constrains accepted-answer replacement; a larger candidate confidence never grants
  acceptance. Explicit operator acceptance admits a claim without raising its authority. Acceptance
  reason (`explicit`, `recurrence`, `verification`, `correction`) and supporting provenance are
  recorded independently of authority.
- MCP `claim` and `correct` create agent candidates. A reported human correction is a proposal,
  never self-acceptance or human endorsement. Only authenticated operator correction creates a
  runtime human assertion; only operator-run successful verification grants runtime verified authority.
- Check failure affects eligibility, not lifecycle or historical authority provenance. An unconfirmed
  higher-authority current answer cannot be superseded by a weaker candidate as an implicit fallback.
- Rejected and superseded claims are terminal at every authority. Group revisions fence plans;
  claim/configuration/attempt tokens fence verification. Correction, supersession links, events and
  idempotent receipts commit atomically. Verification never changes assertion recency.
- The operator's `kypp correct` and `kypp verify` are the runtime writers of human and verified
  authority. Hosted writes use authenticated `POST /v1/operator` and `KYPP_OPERATOR_TOKEN`, separate
  from OAuth, `KYPP_API_TOKENS` and `KYPP_SYNC_TOKEN`; unset means 404, and MCP cannot reach it.
  Local operator commands use the same domain contract through their local adapter. Correction
  supersedes the exact group's prior live assertions, including pending candidates. Every operator
  mutation and its audit record/receipt commit together; hosted audit retains `defrag_log` integration.
- The hosted Worker's authenticated `POST /sync` is the one other writer of human and verified authority: a separate import path for ratified Brief revisions, not built yet; the contract binds when it lands (amended 2026-10-11, ratified by Vu): person-ratified revisions receive human authority and model-ratified revisions receive verified authority, derived from the trusted archive stamp, not caller-selected authority. A model ratification is stamped `ratified_via: model`, so a consumer can tell it apart from a deterministic `verify` result. None of these privileged paths is available through MCP. No other writer is authorized.
- When `POST /sync` lands, the org's CI calls it after `brief ratify` publishes a revision, authenticated by `KYPP_SYNC_TOKEN` (a Worker secret apart from agent OAuth grants and `KYPP_API_TOKENS`; unset means the route answers 404; never reachable through `/mcp`). It imports an accepted org claim holding the pinned `doc://` ref and the one-line rule through its own idempotent command, preserving ratifier provenance and history across revisions. Every sync is logged with the ref and the stamp. No MCP tool passes `authority="human"` or `"verified"`: on the agent surface, "a human told me" is the agent's word, and one confused or prompt-injected agent must not be able to override verified facts across the swarm. MCP `correct` creates an agent candidate replacement proposal instead.
