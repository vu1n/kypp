# Cooperation context, v1

Frozen wire contract for portable handoffs and evidence references. Python
(`kypp/cooperation.py`) and the Worker (`worker/src/cooperation.ts`) implement
this document. The golden corpus is `worker/test/fixtures/cooperation/v1.json`.

This slice is the schema, validators, repository seam, and fixture. It does not
store rows, register MCP tools, rank with a model, or change `authority-order@0008`.
The claim-command adapters landed separately in #44 and remain staged; cooperation
records do not activate them or depend on live-store cutover.

Governing decisions, pinned:

- `doc://kypp/memory-scope-decay@0011#scope-keys-decay` — these records are not
  claims. Wall-clock expiry does not delete, promote, widen, or corroborate them,
  and it does not revive a wall-clock TTL for durable claims.
- `doc://kypp/agent-mcp-surface-compose@0003#compose-surface` — `append_context`
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

A conflict diagnostic requires all three causal-key fields: producer namespace and
original record ID use the provenance token grammar; original run ID is a token or
explicit null. `record_ids` contains 2–16 distinct full lowercase 32-hex Kypp IDs;
`fingerprints` contains 2–16 distinct lowercase 64-hex SHA-256 strings (no prefix).
These are independent sets, not positional pairs: several citations can represent
one payload variant. Diagnostics report conflicts, not verified truth. The validator
checks structure and bounds, not the underlying evidence or artifact contents.

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

`CooperationRepository` is `append`, `getByIds`, and `search` (Python: `append`,
`get_by_ids`, and `search`). Every call takes the authenticated principal, the
resolved origin, and the server clock. `search` runs inside the store. `getByIds`
uses the same principal and origin boundary, keeps expired rows, and does not hide
superseded rows. Neither method calls a model or returns another principal's ids.

## Selection

Selector version `cooperation-fts5-bm25-v1`. The index is FTS5, the same family
claim recall uses in `worker/src/store.ts`. `claims_fts` omits a tokenizer clause
and therefore uses SQLite's default, `unicode61` (case-insensitive, diacritics
removed). Cooperation names that tokenizer:

```sql
CREATE VIRTUAL TABLE cooperation_fts USING fts5(
  record_id UNINDEXED,
  repository,
  candidate_revision,
  body,
  tokenize = 'unicode61'
);
```

`repository` and `candidate_revision` are the work ref. `body` is the handoff
text, or the evidence requirement, spec, outcome class, and label, unit, value,
reason, or statement. This mirrors the hosted Worker's FTS5 path. Local claim
retrieval now depends on the adapter: sqlite3/libsql use FTS5, while pyturso uses
`LIKE` for its lexical component (see [local-store parity](local-store-parity.md)).

`ftsQuery` extracts runs of Unicode letters, combining marks, numbers, and
underscores, quotes each run, and OR-joins them, matching the claim tokenizers in
`memory.ts` and `repository.py`. Marks stay attached so decomposed words such as
`nai\u0308ve` reach `unicode61` intact and match `naïve` after accent folding.
Quotes, `*`, `NEAR`, column filters, and boolean
operators never reach `MATCH`. A query with no tokens is browse, not `MATCH *`.
An unmatched token list returns no FTS rows and is success, not an error.

SQLite rejects `MATCH` inside `OR`. The store therefore runs two filtered
statements. Both apply principal, origin, audience, `created_at <= cutoff`,
`expires_at > now`, kind, and supersession-at-cutoff before any ranking, and
each one `ORDER BY`s its own rows and `LIMIT`s them to the candidate window plus
one. That extra row is only how the merge sees that the window is capped. Neither
statement materializes the rest of the population.

An exact work-ref match uses whichever fields the caller supplied. A repository
alone matches that repository at any revision. A `candidate_revision` alone
matches that revision in any repository. Both together match both. Browse uses
the same predicate to put those rows first.

The MATCH statement orders exact rows ahead of other hits, then `bm25()`
ascending, then id, and returns at most the window plus one. The exact statement
returns at most the window plus one ids, ordered by id. The merge keeps exact
matches first (their `bm25()` when they also matched, otherwise id), then the
other hits by `bm25()` ascending, then id ascending, and keeps the window. The
ratified maximum is still 100 authorized candidates. That window is the top 100
by this order, not the 100 newest, so an older relevant row stays findable. A
better-scoring row the caller cannot see is not in either statement, so it never
appears and never takes a slot. A capped window sets `window_capped` and clears
`candidate_window_exhaustive`. An empty authorized result is success. A failed
read is not evidence that nothing matched.

`validateContribution` returns those rebuilt records. Omitted nullable envelope
fields (`client_label`, `session_id`, `reply_to`, `supersedes`) come back as
null, and `budget_bytes` is the canonical size of that normalized contribution.
Contribution `as_of` and `cutoff` must be real UTC timestamp strings in
`YYYY-MM-DDTHH:MM:SSZ` format; values are never coerced from arrays or other types.
The append JSON schemas also accept explicit null `reply_to` and `supersedes`.
Omitting either link and spelling it null have the same normalized retry digest.
Malformed diagnostic cases are shared by both suites in
`worker/test/fixtures/cooperation/diagnostics.json`; existing v1 golden hashes stay unchanged.
Timestamp rejection and Unicode FTS regression cases are shared in the neighboring
`regressions.json`, including resealed malformed contributions and actual SQLite lookups.

## Out of this slice

No D1 table, no MCP registration, no change to briefing, recall, claim, expand,
correct, `/sync`, or `mark_verified`. The local MCP server does not gain tools.
Huddles stays unconnected. Semantic ranking may replace the lexical key later
without widening the audience or the byte caps.
