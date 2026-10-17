---
id: verify-operator-only
project: kypp
type: decision
status: active
signoff: required
title: "Verify commands are an operator capability, never minted by agents"
related_code:
  - "kypp/verify.py"
  - "kypp/mcp_server.py"
  - "kypp/shell.py"
---

<!-- brief:anchor operator-only-verify -->
## Only an operator attaches or runs verify commands

A claim's `verify` field is arbitrary shell that `kypp verify` executes. Only a human operator attaches one (`kypp remember --verify`, `kypp correct --verify`), and only an operator runs `kypp verify`.

**Why.** An agent-minted verify command on a shared store is remote code execution for whoever runs the next sweep.

### Invariant
- MCP tools never accept a `verify` argument.
- `kypp verify` records timeout/spawn failure as inconclusive execution and never aborts the sweep.
  A failed or inconclusive check makes a checked claim unconfirmed without changing its lifecycle.
- Normal settled reads require a passing applicable check when a verifier is configured, including
  for human assertions. A new configuration starts unconfirmed; a later pass can restore eligibility
  only while the claim is live. Explicit rejection and supersession are terminal for every authority.
- A result must match the claim revision, immutable verifier configuration and current server-issued
  attempt. Starting a newer attempt fences older completions; starting alone retains the last
  completed verdict. Duplicate completion returns its original receipt and cannot apply twice.
- Check completion never refreshes assertion recency or human endorsement. Passing a check may
  accept a candidate at verified authority; it cannot grant human authority.
