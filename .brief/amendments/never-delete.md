# Amendment proposal: never-delete

**Decision:** doc://kypp/append-only-history@0001#never-delete
**Proposed by:** Codex, at the user's request. **Date:** 2026-10-17.

## What should change

Keep no deletion and historical expansion. Make rejected and superseded terminal at every authority.
A mistaken retirement requires a fresh linked assertion. Check evidence qualifies a live assertion's
eligibility separately, so scheduled recovery does not revive retired rows. The exact proposed text
is in the accompanying decision diff.

Activation is staged: terminality and orthogonal checks bind only after complete store-adapter
cutover. Retention binds throughout; the decision explicitly permits legacy verification before then.

## Why

Current verification revives manually rejected and superseded nonhuman claims. Its human-only
exception makes replacement depend on historical authority. The user prefers a consistent contract.

## Code that needs it

Future store commands, rejection, correction and verification. This PR adds only an unconnected
reference model and tests; production behavior is unchanged.

## Impact / risk

Legacy retired claims remain retired; do not guess which were rejected by checks. Preserve every
row/handle. Live checked claims can recover eligibility. Migration and writer upgrades follow
ratification; this PR executes neither.
