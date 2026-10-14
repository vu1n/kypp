# Amendment proposal: authority-dominates

**Decision:** doc://kypp/authority-order@latest#authority-dominates
**Proposed by:** Vu Nguyen (instructed in chat)   **Date:** 2026-10-14

## What should change

Replace the two "only local writer" bullets in `authority-order@0006` with one rule that does not
assume a local store:

> The operator's `kypp correct` and `kypp verify` are the only writers of `authority=human` and
> of runtime-verified `authority=verified`. They run on the operator's machine and write through
> the hosted Worker's authenticated `POST /v1/operator`, using `KYPP_OPERATOR_TOKEN`. That's a
> Worker secret apart from agent OAuth grants, `KYPP_API_TOKENS` and `KYPP_SYNC_TOKEN`. When it's
> unset, the route answers 404, and it's never reachable through `/mcp`. Every operator write is
> logged in `defrag_log`. When the CLI targets a local Worker (the documented workaround), the
> same route and token apply. `POST /sync` stays as written. No other writer is authorized, and
> no MCP tool passes `authority="human"` or `"verified"`.

`kypp correct` still supersedes the subject's other live claims. The `POST /sync` bullet, the
model-ratification stamp and the MCP `correct` behaviour are unchanged.

## Why

Vu chose to drop the local store: the `kypp` CLI talks to the hosted Worker, and running the same
Worker locally under workerd is the documented fallback. `@0006` names `shell.correct_main` and
`MemoryStore.mark_verified` as the only *local* writers of human and verified authority. With no
local `MemoryStore`, those commands still run on the operator's machine, but their writes have to
land on the Worker. The Worker's only privileged writer today is `POST /sync` imports, and MCP must
never write `human` or `verified`. So the operator needs its own authenticated route, kept apart
from agent credentials the same way `/sync` is.

## Code that needs it

- Worker: `POST /v1/operator` behind `KYPP_OPERATOR_TOKEN`, with tests mirroring the `/sync`
  contract (404 when unset, never on `/mcp`, every write logged in `defrag_log`). An additive
  migration restores the `verify` column that `0001` dropped as local-only, which
  `verify-operator-only@0002` needs.
- CLI: an HTTP backend (`KYPP_URL` plus the operator token) for `recall`, `briefing`, `remember`,
  `correct` and `verify`; `pyturso` and the local store are removed.
- Context pins on the authority-order sites move to the new revision.

None of that lands before this is ratified.

## Impact / risk

The set of human and verified writers stays the operator's two commands plus `/sync` imports;
only where they write changes. The new risk is a second Worker secret that grants human
authority: a leaked `KYPP_OPERATOR_TOKEN` lets its holder override any claim. That is the same
exposure as a leaked `KYPP_SYNC_TOKEN`, and it gets the same handling (separate secret, 404 when
unset, every write logged).


---
ratified_rev: 0007
ratified_by: Vu
