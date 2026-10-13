# Amendment proposal: authority-dominates

**Decision:** doc://kypp/authority-order@latest#authority-dominates
**Proposed by:** Vu Nguyen (instructed in chat)   **Date:** 2026-10-13

## What should change

Reconcile the verified-writer contradiction in `authority-order@0004`. The `/sync` bullet
permits a model-ratified revision to land at `verified`, and the next bullet says only
`MemoryStore.mark_verified` sets `authority=verified`.

The ratified rule, in the decision's own wording:

`MemoryStore.mark_verified` (the `kypp verify` path) is the only local runtime-verification path that grants verified authority. The hosted Worker's authenticated `POST /sync` is a separate import path for ratified Brief revisions: person-ratified revisions receive human authority and model-ratified revisions receive verified authority, derived from the trusted archive stamp, not caller-selected authority. A model ratification is stamped `ratified_via: model`, so a consumer can tell it apart from a deterministic `verify` result. Neither privileged path is available through MCP. No other writer is authorized.

Preserve the separate sync credential (`KYPP_SYNC_TOKEN`, apart from agent OAuth grants and
`KYPP_API_TOKENS`; unset answers 404), pinned revision provenance (the upsert holds the
pinned `doc://` ref), the audit trail (every sync is logged in `defrag_log` with the ref
and the stamp), and the ratification stamp. Everything else in `@0004` stays, including
the operator's `kypp correct` as the local writer of `authority=human`.

## Why

Both sentences are in force in `@0004`, so a reader cannot tell which writer of `verified`
is authorized. Sync imports a ratified Brief revision; `mark_verified` is a local check.
They are different paths, and the second must not deny the first.

## Code that needs it

None. Runtime code stays. Spec checks that pin `@0004` writer bullets stay too: ratifying
a policy does not create positive evidence. The verified-writer row can be re-evaluated
against `@0005` in a follow-up.

## Impact / risk

A model-ratified sync and a passing `verify` can both yield `authority=verified`. The
archive stamp is what separates them. No MCP writer is added, and authority is still not
caller-selected.


---
ratified_rev: 0005
ratified_by: Vu
