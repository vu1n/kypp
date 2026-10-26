---
id: portable-cooperation
paths:
  - "kypp/cooperation.py"
  - "worker/src/cooperation.ts"
  - "worker/test/cooperation-contract.test.ts"
  - "worker/test/fixtures/cooperation/v1.json"
  - "worker/test/fixtures/cooperation/diagnostics.json"
  - "worker/test/fixtures/cooperation/regressions.json"
  - "tests/test_cooperation_contract.py"
  - "docs/cooperation-context.md"
entry:
  - "kypp/cooperation.py"
  - "worker/src/cooperation.ts"
tests:
  - "worker/test/cooperation-contract.test.ts"
  - "tests/test_cooperation_contract.py"
---
# Portable cooperation

Short-lived handoffs and evidence references shared by the Python and Worker
contracts. They are not claims. The store, selector, and hosted MCP wiring are
later slices; this feature is the frozen schema, validators, and fixture.

## Gotchas
- Do not write these records into `memory_claims`. Wall-clock expiry never deletes a
  row and does not change durable-claim clocks.
- `append_context` and `query_context` have no authority parameter. The input schemas
  set `additionalProperties` false.
- Canonical JSON is sorted keys, no whitespace, and safe integers only. Both languages
  must match `worker/test/fixtures/cooperation/v1.json`.
- The idempotency digest is the normalized client payload. `derived_from` is sorted.
  An omitted ttl and an explicit 86400 are the same digest. Evidence identity is a
  different hash and ignores bridge order.
- A missing link and a forbidden link both fail as `INVALID_RECORD`.
- `legacy_false` is `inconclusive_execution`, not `invalid_candidate`.
- Search is FTS5 `unicode61` with `bm25()`, selector `cooperation-fts5-bm25-v1`.
  SQLite rejects `MATCH` inside `OR`, so exact work-ref rows are a second filtered
  read. Each statement `LIMIT`s to the window plus one before the merge. The extra
  row only detects the cap. Exact match uses whichever of repository and
  `candidate_revision` was supplied. The 100-row cap is the maximum,
  ranked by exact match then `bm25()` then id, not the 100 newest rows.
- `validateContribution` returns the rebuilt records, with omitted nullable fields
  filled. `budget_bytes` is the canonical size of that normalized object.
- Contribution timestamps must be strings without coercion: `String([timestamp])`
  hides malformed JSON that Python rejects. Shared cases in `regressions.json`
  reseal the byte budget before testing rejection.
- Cooperation query tokens must retain combining marks, as claim tokens do. Splitting
  decomposed `nai\u0308ve` into `nai` and `ve` loses a valid `unicode61` match;
  `regressions.json` covers both token output and SQLite retrieval in both languages.
- Append schemas accept null lineage links just like runtime validators; null and
  omission must retain the same retry digest.
- Conflict diagnostics validate provenance tokens, full record IDs and bare SHA-256
  fingerprints. Both lists contain 2–16 distinct entries; they are independent sets,
  not paired arrays. Reseal negative test contributions so the byte budget cannot
  mask a diagnostic validation bug. Shared malformed cases live in `diagnostics.json`.
- Fixture case `floating-brief-ref` stores `doc://kypp/memory-scope-decay@latest#scope-keys-decay`
  so both validators reject it. It is a counterexample, not a citation.
