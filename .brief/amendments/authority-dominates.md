# Amend `authority-dominates`: an operator route on the hosted Worker may write human authority

Proposed 2026-10-11. This follows `memory-scope-decay` §9 (ratified 2026-10-11), which says an org
owner ratifies an org proposal, promotes a `user` claim to the org, or syncs a ratified org decision,
and that each of these lands at `human` authority.

## Why change

The invariant names one writer of `authority=human`: the local operator's `kypp correct`
(`shell.correct_main`). That writer works on the local store only. Org claims live in the hosted
Worker's D1, where today nothing can write human authority, so §9 can't be built without a second
writer. The only alternative is landing org claims at agent authority, which would let any number of
agreeing agent claims, or one MCP `correct`, outrank an org rule a person ratified.

## What changes

Add one invariant and widen one:

- **A second human writer, outside MCP.** The Worker's operator route (`POST /operator`) may write
  `authority=human`, for §9's three actions only: ratify an org proposal, promote a `user` claim to
  an org, and sync a ratified org decision. It is authenticated by `KYPP_OPERATOR_TOKEN`, a Worker
  secret that is separate from agent OAuth grants and `KYPP_API_TOKENS`. With the secret unset, the
  route answers 404. It is never reachable through `/mcp`.
- **The rest stays.** No MCP tool passes `authority="human"` or `"verified"`. MCP `correct` still lands
  at agent authority. Only `kypp verify` sets `verified`. Every operator write is logged in
  `defrag_log` with the action and the claim it came from.

The operator token is a credential that can override every agent claim in a scope. It lives in
`wrangler secret`, never in a repo, a claim or an agent's environment, so an agent with repo or MCP
access still can't mint human authority.
