---
id: human-authority-operator-only
project: kypp
type: decision
status: active
title: "Human authority is an operator capability, never minted over MCP"
related_code:
  - "kypp/mcp_server.py"
  - "kypp/shell.py"
---

<!-- brief:anchor operator-only-human -->
## Only the operator's CLI writes human authority

`authority=human` outranks every agent claim, any amount of corroboration, and verified claims. Only the operator's `kypp correct` writes it. The MCP `correct` tool records what a human told the agent as an accepted claim at agent authority and top confidence (`HUMAN_CORRECTION_CONFIDENCE`). That beats weaker agent claims on the subject, but never a verified or human one.

**Why.** On the agent surface, "a human told me" is the agent's word. If an agent could mint the top authority tier, one confused or prompt-injected agent could override verified facts across the whole swarm. This is the same reasoning as `doc://kypp/verify-operator-only@0001#operator-only-verify`.

### Invariant
- No MCP tool passes `authority="human"` or `authority="verified"` to the store.
- `kypp correct` (`shell.correct_main`) is the only caller that writes `authority="human"` outside module self-tests.
