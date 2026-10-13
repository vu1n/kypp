# Amendment proposal: authority-dominates

**Decision:** doc://kypp/authority-order@latest#authority-dominates
**Proposed by:** Vu Nguyen (instructed in chat)   **Date:** 2026-10-13

## What should change

Qualify the human-writer rule in `authority-order@0005`, and state `POST /sync` as a planned
route. No new policy.

- The operator's `kypp correct` (`shell.correct_main`) is the only **local** writer of
  `authority=human`, and it still supersedes the subject's other live claims. Authenticated
  `POST /sync` is the sole other writer of human authority, and only as an import of a
  person-ratified revision. That mirrors the verified bullet: `MemoryStore.mark_verified` is
  the only local verification path, and `/sync` is the separate import.
- `POST /sync` is not built. The decision names the contract that binds when the route lands
  (credential, pinned `doc://` ref, `defrag_log` stamp, archive authority). It does not
  describe a route the Worker already serves.

## Why

`@0005` gives person-ratified sync imports human authority, and the preceding bullet still
says only `shell.correct_main` writes `authority=human`. The Worker has no `/sync` route;
`worker/README.md` already says it is not built. Present tense in the decision overclaims
the code.

## Code that needs it

Comment pins on the authority-order Context sites, re-checked against this revision. No
runtime change. Speccheck stays on its frozen `@0004` pins.

## Impact / risk

The writer set is the one `@0005` already named, with the local/import split made explicit
and the unbuilt route marked as a contract. Nothing new may write `human` or `verified`.


---
ratified_rev: 0006
ratified_by: Vu
