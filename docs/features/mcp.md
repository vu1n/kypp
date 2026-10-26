---
id: mcp
paths:
  - "kypp/mcp_server.py"
entry:
  - "kypp/mcp_server.py"
tests:
  - "tests/test_mcp_contract.py"
  - "tests/test_mcp_recurrence.py"
---
# MCP server

`kypp serve` runs the agent-facing surface over stdio, or over HTTP with `--http`. It is bound
to one project, derived from `KYPP_REPO_ROOT`.

## Gotchas
- `serve --http` has no authentication. Keep it on localhost.
- The MCP `correct` tool lands at agent authority, not human. It is not a writer of `human` or
  `verified`. Local human authority is the operator's `kypp correct`; local verified authority is
  `MemoryStore.mark_verified`. Authenticated `POST /sync` is not built yet; when it lands, it is the
  import path for externally reviewed content at the archive stamp's authority. Authority
  outranks confidence and corroboration: agent < verified < human.
- No agent call self-accepts. Every MCP `claim`, decisions included, lands as a candidate stamped
  `session:<id>`; it is accepted when a second session claims the subject or an operator accepts it.
  The stamp and usage logging need a session id: stdio mints one per process, `--http` needs
  `KYPP_SESSION`.
