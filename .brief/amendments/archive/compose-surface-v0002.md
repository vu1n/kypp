# Amendment proposal: compose-surface

**Decision:** doc://kypp/agent-mcp-surface-compose@0001#compose-surface
**Proposed by:** Astra planning task for portable cooperation context
**Date:** 2026-10-13
**Status:** Proposal only; human ratification required. This file grants no authority.

## What should change

Permit a separate, narrow hosted cooperation surface alongside durable memory:

- append_context: append one versioned client-authored handoff or evidence-reference record.
- query_context: return bounded, source-linked records selected under current authorization;
  exact-ID historical access is a bounded mode of this same operation.

These operations supplement today's briefing/recall/claim/expand/correct implementation.
`append_context` and `query_context` take no authority parameter. `/sync`, `correct`, and
`mark_verified` are unchanged under authority-order@0006.
This amendment does not claim compose is already implemented or require the complete
four-verb migration to land with cooperation records. State explicitly that the Python
backend/local MCP surface is not expanded in this first slice.

Kypp may filter, rank and retrieve by task relevance, including semantic selection later.
Its output is a memory contribution. It does not own role profiles, required human intent,
invocation construction, a sealed HCP, task assignments, task completion, approvals or agent
execution. The caller authors the handoff and assembles its own final context. Future role
bias/compression in compose remains a retrieval design question, not a Kypp invocation
compiler mandate.

For v1, adopt the companion scope-keys-decay record/privacy/lifecycle contract and the
proposed wire specification reproduced in the accompanying proposal PR. PC-002 will freeze
docs/cooperation-context.md only after human ratification. Enforce input bounds, a maximum
100-record authorized candidate window, at most 30 returned records and at most 32 KiB of
serialized contribution including its selection manifest. Record selected IDs/revisions,
cutoff, selector version and safe bounded omission information. Never dump the whole store,
truncate away provenance, or rank inaccessible records.

The initial implementation uses deterministic exact/lexical selection and no paid model.
Semantic selection can improve ranking later without changing authority, source visibility
or response bounds; it must never silently widen the audience.

Do not add inbox delivery guarantees, presence, acknowledgments, locks, a workboard task
database, scheduler, background wake loop, automatic status synchronization or an Advisor.
A workflow reference points to the system that owns its task state.

Extend related_code to hosted mcp.ts and the cooperation modules, while retaining the
existing local MCP coverage. Leave existing durable claim tooling and authority intact.

## Why

Short-lived cooperation context has different semantics from durable claims. Two orthogonal
operations provide the minimum useful transport across standalone sessions and optional
workflow consumers. Claiming status through the durable write gate or implementing an
invocation compiler would blur that boundary.

Huddles already owns its profiles, generic selection/budgeting and HCP sealing. The first
adapter/replay can prove structural conformance without creating another compiler or
mislabeling Kypp records as CocoIndex retrieval.

## Code that needs it

worker/src/cooperation.ts, cooperation-store.ts, cooperation-query.ts and mcp.ts; additive
D1 schema; focused contract/store/query/MCP/integration tests. Client adapters own authored
handoffs and final context assembly. Huddles code remains pure and unconnected, with no
production import or HCP schema changes. The accompanying proposal PR reproduces the
validated portable-cooperation task footprints; Kypp's root PLAN.md belongs to checker work.

## Impact / risk

Tool count increases by two, so descriptions must make the durable claim versus temporary
cooperation choice explicit. Current clients remain compatible and old claim behavior is
unchanged. Runtime input validators, not prose descriptions, enforce boundaries.

No efficacy is established by building the surface or passing replay fixtures. A future
measured experiment needs separate budget authorization. Live Huddles use requires typed
non-event provenance, current source policy checks, an approved HCP representation and
outbound audience authorization; this amendment does not grant those.

## Ratification and rollout

Only a human may ratify this Kypp architectural amendment. Publish an approved version
together with scope-keys-decay; record the exact revision/archive stamp. No dependent code
starts while either remains a proposal. Re-read the published text, reconcile the plan with
any changes, then run brief doctor and pin reviewed references before implementation lands.


---
ratified_rev: 0002
ratified_by: Vu
