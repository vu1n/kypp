# Amendment proposal: operator-only-verify

**Decision:** doc://kypp/verify-operator-only@0002#operator-only-verify
**Proposed by:** Codex, at the user's request. **Date:** 2026-10-17.

## What should change

Preserve operator-only attachment/execution of shell and the no-MCP boundary. Failed or inconclusive
checks make assertions unconfirmed without rejecting them. Checked assertions need an applicable
pass for settled reads, even at human authority. Bind completion to claim revision, configuration
and current issued attempt. The decision diff contains the exact proposed invariants.

The new result/protocol rules bind only after complete store-adapter cutover. Operator-only shell
binds throughout; legacy boolean/lifecycle handling stays permitted until that boundary.

## Why

The boolean conflates inability to execute with falsity and intentional rejection. Old callbacks can
overwrite newer outcomes. Separate check evidence plus fencing preserves safe scheduled recovery.

## Code that needs it

Future run_check outcomes, verification CLI, operator begin/complete protocol, reads and both stores.
The executable reference is unconnected to these runtime paths.

## Impact / risk

Legacy checked rows need rechecking; a newly configured check gates reads until its first pass.
Old ID/boolean callbacks need an upgrade-required response. This PR executes no shell and no agent
can attach a verifier. Automatic admission fences the generation at check issuance. A blocked
candidate pass records evidence without privileged authority; a fresh check can reassess it. Cutover
must include a full operator verify sweep and report still-unconfirmed groups.
