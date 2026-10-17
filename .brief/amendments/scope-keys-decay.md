# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0010#scope-keys-decay
**Proposed by:** Codex, at the user's request. **Date:** 2026-10-17.

## What should change

Define the initial four-state claim contract with terminal retirement and orthogonal check evidence.
Each group has one accepted head plus pending candidates. Recurrence requires distinct original
sources, no echoes, established agreement and at least one observation from the current head generation.
Older live candidates can support a new admission; they are not permanently stranded. Unknown/conflicted judgment
preserves alternatives. Require atomic versioned commands and receipts. Exact text replaces §6;
event-clock decay remains future work.

These target lifecycle/admission/command rules bind only after complete store-adapter cutover.
Existing scope, origin, pin and room boundaries bind throughout; the decision names the interim legacy
handling rather than retroactively requiring unimplemented behavior from current main.

## Why

A held group can lose conflicting candidates; count/newest-time can miss state changes. The user
wants a small executable contract before adapters. Recurrence is neither independent identity nor truth.

## Code that needs it

Future consolidation, placement, source/exposure stamps, reads and all adapter writes. The isolated
reference specifies these changes without connecting them to production.

## Impact / risk

No scope, room, pin, origin or authorization widening. Cooperation remains separate. Decay, docs
promotion, execution leases and terminal UI are not built here. Unknown paraphrases wait; exact
normalized recurrence still works. Migration and full writer alignment follow ratification.
