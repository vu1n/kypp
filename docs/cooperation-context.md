# Cooperation context, v1

Frozen wire contract for portable handoffs and evidence references. Python
(`kypp/cooperation.py`) and the Worker (`worker/src/cooperation.ts`) implement
this document. The golden corpus is `worker/test/fixtures/cooperation/v1.json`.

This slice is the schema, validators, repository seam, and fixture. It does not
store rows, register MCP tools, rank with a model, or change `authority-order@0006`.

Governing decisions, pinned:

- `doc://kypp/memory-scope-decay@0005#scope-keys-decay` — these records are not
  claims. Wall-clock expiry does not delete, promote, widen, or corroborate them,
  and it does not revive a wall-clock TTL for durable claims.
- `doc://kypp/agent-mcp-surface-compose@0002#compose-surface` — `append_context`
  and `query_context` are a bounded memory contribution. They take no authority
  parameter. The caller keeps task authority.

## Records

`schema_version` is the integer `1`. `kind` is `handoff` or `evidence_reference`.
Clients send `AppendContextInput`. The server stamps `CooperationRecord`: id,
authenticated principal, client label, session id, resolved origin, `created_at`,
`expires_at`, and `audience: {kind: "principal", principal}`. Client labels are
provenance. A project name is not an ACL. Unknown origins are rejected for this
surface only; durable-claim unsorted writes stay as they are.

A record is append-only. `reply_to` and `supersedes` are full 32-hex ids of other
records. A correction is a new row. Expiry means not fresh (`expires_at > now` is
fresh; equality is expired). It does not mean failure, revocation, or completion.
Normal reads omit expired rows. Exact-id reads set `include_expired: true`, return
the same bytes, and label freshness. An old cutoff never restores access.

## Evidence

An evidence reference keeps the producer namespace, original record id, run id,
tested requirement, pinned spec ref, full candidate revision, evaluator identity
and revision, and one immutable source. The source is an artifact digest or a
positioned session log. The outcome is exactly one of:

| class | meaning |
|---|---|
| `valid_result` | a real result, including a valid non-improvement (`value: 0`) |
| `invalid_candidate` | the candidate itself failed, and the reason is required |
| `inconclusive_execution` | `timeout`, `spawn_failure`, `evaluator_failure`, `infrastructure`, or `legacy_false` |
| `assertion` | no independent evaluation; evaluator and run may be absent |

Evaluated classes require `evaluator_ref` and `run_id`. A boolean `false`, an empty
reason, or any other spelling is rejected. `legacy_false` is inconclusive. Only
`valid_result` may carry `value`, `unit`, and `label`. Numeric values are safe
integers. There is no `measured`, verifier command, capability, or approval field.
Storing a result does not mint `verified` or `human` authority and is not corroboration.

The causal key is `(producer_namespace, original_record_id, original_run_id or null)`.
The evidence fingerprint is SHA-256 of the canonical original payload: requirement,
spec, candidate revision, evaluator, run, source (digest and position included),
outcome, and result value/unit/label. Bridge ids, transport timestamps, and
`derived_from` order are not part of it. Equal key and equal fingerprint are one
observation with multiple citations. The same key with a different fingerprint is
`conflicting_reported_evidence`; both variants stay.

## Bounds

| Bound | Value |
|---|---|
| Stored record | 8 KiB UTF-8 canonical JSON, including the server envelope |
| Client strings | 1,024 UTF-8 bytes; source locators 512 |
| Source refs / lineage refs | 16 of each |
| TTL | default 24h; inclusive 60 seconds through 7 days |
| Result count | default 12; integer 1–30 |
| Query text | 512 UTF-8 bytes |
| Response | default 16 KiB; allowed 2–32 KiB of canonical `ContextContribution` |
| Candidate window | 100 authorized rows |
| Exact ids | 30 full ids, no prefixes |
| Population | 10,000 retained rows per principal and origin |

Canonical JSON sorts object keys by Unicode code point, emits no extra whitespace,
and accepts only safe integers, strings, booleans, null, and arrays. The response
budget is that encoding of the whole contribution, including `budget_bytes` and the
manifest, and excluding JSON-RPC framing. `sealContribution` fills `budget_bytes`
until it equals that length. A record that does not fit is omitted whole.

Quota exhaustion returns `CAPACITY_EXCEEDED` and deletes nothing. An identical
idempotency key is resolved before the cap, and its original receipt is returned
without recomputing TTL. The digest is SHA-256 of the normalized client payload.
Omitting `ttl_seconds` and sending `86400` are the same payload. A changed payload
with the same key is `IDEMPOTENCY_CONFLICT`. `derived_from` is sorted before the digest.

Other codes: `INVALID_RECORD`, `UNKNOWN_ORIGIN`, `UNAUTHENTICATED`, `UNAVAILABLE`.
A missing link and a link the caller cannot see are both `INVALID_RECORD`.

## Repository seam

`CooperationRepository` is `append`, `getByIds`, and `scanCandidates` (Python:
`append`, `get_by_ids`, `scan_candidates`). Every call takes the authenticated
principal, the resolved origin, and the server clock. `scanCandidates` returns at
most 100 rows that are already limited to that principal and origin, with
`created_at <= cutoff` and `expires_at > now`, and it omits predecessors hidden by
a supersession link that existed at the cutoff even if the superseder has since
expired. `getByIds` uses the same principal boundary, keeps expired rows, and does
not hide superseded rows. Neither method ranks or calls a model.

## Selection

`compareSelection` is the v1 order, selector version `cooperation-lexical-v1`.
Exact work-ref matches come first, then case-sensitive lexical hits in the handoff
text or the evidence requirement, spec, outcome, and result, then newer
`created_at`, then ascending id. An empty query does not prefer a lexical hit.
No role input and no paid model. A capped window sets `window_capped` and clears
`candidate_window_exhaustive`. An empty authorized result is success. A failed read
is not evidence that nothing matched.

## Out of this slice

No D1 table, no MCP registration, no change to briefing, recall, claim, expand,
correct, `/sync`, or `mark_verified`. The local MCP server does not gain tools.
Huddles stays unconnected. Semantic ranking may replace the lexical key later
without widening the audience or the byte caps.
