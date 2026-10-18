# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0011#scope-keys-decay
**Proposed by:** Claude, at the user's request. **Date:** 2026-10-18.

## What should change

Add one narrowing to the activation clause. Before cutover, legacy recurrence counts a claim's
adapter-added `session:` stamp and never source ids its MCP caller cited; an unstamped MCP claim
counts no source. Every other legacy lifecycle, deduplication and verification behavior is unchanged,
and no other §6 rule is activated. The exact text is in the accompanying decision diff.

## Why

Legacy local MCP `claim` forwarded caller `source_ids` into the K=2 gate, so one session promoted its
own guess with invented ids, its own observations or a `session:x` string, and could replace an
accepted agent answer the same way. The clause as written requires that legacy behavior until cutover.
The narrowing only withholds acceptance, matching the hosted Worker, which already stores only the stamp.

## Code that needs it

`kypp/arbiter.py` (`_corroboration`/`_sources`) and `kypp/mcp_server.py` (`claim` renames cited ids
`cited:…`), with `tests/test_mcp_recurrence.py`.

## Impact / risk

No scope, origin, pin, room or authority change; nothing new can be accepted. An `--http` server
without `KYPP_SESSION` no longer promotes by recurrence (operator acceptance still works). Distill,
seed and batch claims count as before. Rows written by an unstamped server before the change keep raw
ids until cutover migration.


---
ratified_rev: 0012
ratified_by: vu1n via /ratify (https://github.com/vu1n/kypp/pull/47#discussion_r4306158506); vu1n (merge of #47)
ratified_via: human
