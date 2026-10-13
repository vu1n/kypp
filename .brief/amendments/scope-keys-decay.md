# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0004#scope-keys-decay
**Proposed by:** Astra planning task for portable cooperation context
**Date:** 2026-10-13
**Status:** Proposal only; human ratification required. This file grants no authority.

## What should change

Resolve the Open item about short-lived notes by permitting two separate cooperation
record kinds: handoff and evidence_reference. They are not memory claims.

1. Clients author concise handoffs and evidence references. The server stamps authenticated
   principal, resolved origin, creation time and expiry. Client labels/session identifiers are
   provenance, never independent identity or permission grants.
2. v1 is hosted-only, authenticated-principal-private and exact-origin-filtered. The
   current authenticated Kypp principal is the whole audience. A project label is not an ACL;
   clients cannot widen to groups/global or another principal. Unknown origins are rejected
   for this record surface without changing unsorted durable-claim behavior.
3. Records are append-only. Corrections/replies/supersessions append links to accessible
   records; retry keys are idempotent. Expiry excludes records from normal retrieval, but
   exact-ID historical access retains content/provenance under current authorization.
   Expiry means not fresh; it never implies failure, revocation or task completion.
4. These temporary records use explicit wall-clock expiry, initially default 24 hours,
   minimum 60 seconds and maximum 7 days. This is not the decay policy of durable claims.
   Cooperation records are not memory claims, so their wall-clock expiry does not revive
   the rejected wall-clock TTL for durable claims.
   No automatic row deletion, defrag acceptance, widening, promotion or corroboration.
   A durable lesson requires an explicit separate claim through the existing pipeline.
5. Evidence references retain the original producer identity, run, tested requirement,
   spec/evaluator/candidate revisions and immutable artifact or positioned log reference.
   They distinguish valid_result (including non-improvement), invalid_candidate,
   inconclusive_execution and assertion. Timeout or spawn failure is inconclusive.
   An unqualified legacy false is not a valid experimental negative.
6. Storing or repeating a reported result cannot mint verified/human authority or independent
   corroboration. No measured boolean, verifier command, capability or approval field is added.
   `append_context` and `query_context` take no authority parameter. `/sync`, `correct`, and
   `mark_verified` are unchanged under authority-order@0006.
7. Store references and concise client-authored observations, never full transcripts, raw logs,
   sealed HCP bodies, credentials or signed access URLs. Source references do not confer access.
   Huddles contributors must establish disclosure permission for the entire Kypp audience.
8. Normal and exact-ID retrieval enforce the same principal boundary. Authorization precedes
   filtering/ranking/model use. The response and candidate window are bounded and explicitly
   non-exhaustive when capped. The calling harness retains invocation composition and task authority.
9. Bound the initial implementation at 8 KiB per record, 16 source/lineage references of each
   kind, 30 returned records, 100 candidates, 32 KiB response, and 10,000 retained records per
   principal+origin. Quota exhaustion refuses append without deleting history. Any later
   retention/archive policy is a separate reviewed change.

Extend the decision's related_code coverage to worker/src/cooperation*.ts,
worker/src/mcp.ts and the cooperation migration. Preserve existing sections on WIP,
cross-session candidates, event clocks, claim promotion, org rules and human authority.

## Why

Standalone agents need a portable, short-lived account of attempted work and source evidence
without a room or a durable-claim write. The current Open section already identifies a
separate expiring kind as the possible route. Storing a handoff as a claim would mix status
with durable lessons and risk unwanted promotion.

Huddles can be a consumer/contributor while retaining its own task, approval and visibility
authority. Research about stateless agents motivates testing this boundary; it is not
evidence that this substrate improves decisions or permission to introduce an Advisor.

## Code that needs it

New Worker cooperation types/validators, D1 append-only table/store, bounded selector and
two MCP operations. Standalone replay and an unconnected Huddles adapter consume the frozen
wire contract. The existing claim store, defrag, local Python backend and verifier stay
unchanged. The accompanying proposal PR reproduces the validated portable-cooperation
plan's proposed v1 contract and PC-002 through PC-008 ownership and gates. Kypp's root
PLAN.md belongs to the separate checker work.

## Impact / risk

Principal-private v1 deliberately excludes multi-principal sharing/federation. OAuth owner
may cover more readers than a private Huddles source; export must fail closed when that
audience cannot be proven authorized. Expiry is not erasure or revocation. Retained history
consumes bounded quota and eventually needs a separately designed archive policy.

The active durable-memory rules continue to govern claims. No new authority writer, task
database, scheduler or event transport is introduced. Deployments and live Huddles integration
are outside this proposal's first implementation slice.

## Ratification and rollout

Only a human may ratify this Kypp architectural amendment. Record the published revision and
archive stamp before coding depends on it. Publish this together with the compose-surface
amendment, then rerun brief doctor, verify affected references, and pin code to approved
versions. A proposal, review verdict or generic implementation approval is not ratification.
