# Claim state machine: proposed executable contract

Status: proposed; merging the amendment PR ratifies the decision changes. The reference model in
`specs/claim_state_machine/model.py` is deliberately outside the runtime package. No current store,
CLI, MCP handler or scheduled job implements this contract yet. Tests exercise the proposal, not
production conformance. No schema migration or deployment is part of this first slice.

Ratification and activation are separate: merging adopts the target contract and the explicit staged
activation clauses in each amended anchor. The new runtime transition rules bind a store only after
its migrations, readers, all writers and verification protocol complete cutover together. The stated
legacy behavior remains permitted before that boundary; retention, authorization and operator-only
capabilities bind throughout. A partially upgraded shared store is not an activated adapter. The
follow-up adapter PR must identify the cutover and demonstrate conformance, rather than merely set a
feature flag on one route. This prevents a contract-only merge from declaring unchanged main in violation.

## Domain and transitions

One arbitration group is `(subject, scope, project, owner)`, with owner present for user/room scope.
It has at most one accepted current answer and any number of pending candidates. A checked answer
that is temporarily unavailable remains the current answer; lower authority cannot replace it by
exploiting a failed check. Authorized reads can still show pending candidates as unconfirmed.

Claim IDs are immutable, full, never-reused identifiers. Content and verifier configuration are
immutable assertions. Reassertion creates a new candidate linked to its predecessor, without copying
acceptance or privileged authority. Display labels may change without changing IDs; `subject` is an
arbitration key, not an interchangeable display label. Moving a claim between groups requires a
placement transaction, including both groups' revisions.

| Command | Preconditions and atomic result |
|---|---|
| Propose | Authenticated writer; create a candidate at agent authority, stamped with origin, source, exposure and current-answer identity. Only an operator may attach a verifier. |
| Accept explicitly | Operator; candidate becomes accepted with reason `explicit`, authority unchanged. It can replace the current answer only at equal or higher authority. |
| Accept by recurrence | Maintenance; at least K (minimum/default 2) distinct claims and original sources, no echoes, agreement, and at least one observation from the current-answer generation. Older live candidates remain usable as additional support. Accept the survivor; supersede only its agreement-backed duplicates and the replaceable current answer. |
| Correct | Operator; create a human assertion and atomically accept it, supersede every prior live assertion in the exact group, and write successor links/events. Confidence cannot defeat the correction. |
| Reject | Operator, or late maintenance rejection of an unaccepted agent candidate; retire it permanently. Maintenance cannot withdraw an accepted answer, including operator admission at agent authority. Operator rejection of the head clears it without reviving history. |
| Begin check | Operator; live claim with a configured verifier. Issue a claim-revision/configuration/attempt token. |
| Complete check | Operator; matching current token and live claim. A pass records evidence and can accept a candidate at verified authority; human stays human. A failed/inconclusive check records evidence but does not change lifecycle. |

Lifecycle stays `candidate | accepted | rejected | superseded`. Both retirement states are terminal
at every authority. No set-status command is exposed. Supersession identifies its successor.
An operator reasserts a mistaken rejection as a fresh linked assertion; a check never does that.

Acceptance reason (`explicit | recurrence | verification | correction`) is historical provenance,
not a rank or a caller-selected capability. Later checks do not overwrite the initial reason.
Authority remains `agent < verified < human`: recurrence admits memory but grants no trust tier.
`verified` records verification-based authority; only an applicable passing check makes it currently
confirmed. Among eligible replacements at one authority, explicit commit order replaces confidence,
source-count and timestamp tie-breaking. An unaccepted candidate never wins solely by a larger number.

The operator's `remember --accept` maps to explicit admission; `correct` asserts a human replacement.
Explicit agent-level admission prevents later classifier rejection, not agreement-backed replacement
by new agent-level recurrence under the normal authority rules.
MCP `correct` becomes a candidate replacement proposal, like `claim`, and reports that outcome plainly.
No MCP call accepts, attaches shell, or mints human/verified authority. Existing tool names may remain
compatibility aliases. Authentication and project/room reach are checked before any command runs.

## Verification, recurrence and evidence

Normal settled-memory eligibility is:

```
authorized_to_read AND status == accepted
AND (no configured check OR latest applicable completed result == passed)
```

An attached check starts unknown. This also gates an operator correction with a new verifier until
the first pass. A subsequent check starts without hiding the last completed pass. A failed command,
timeout or spawn failure makes the assertion unconfirmed; diagnostics distinguish failed checks from
inconclusive execution. A later pass restores a live answer's eligibility. Neither event revives a
rejected/superseded assertion. Checks never refresh assertion recency or human endorsement.

A token binds claim ID, claim revision, verifier digest, server-issued attempt and head generation at
check issuance. A new operator check can deliberately reassess an older pending assertion against the
current head. Starting a newer attempt fences every older one, even if an older worker is still
running. Completing an attempt
consumes it. Group CAS and attempt fencing are checked at the mutation boundary, not in a terminal or
client callback. A passing candidate check that is blocked by a newer/stronger head records evidence
but leaves authority at agent and acceptance unset. It cannot later bypass that head through explicit
acceptance. A deliberately issued fresh check can accept it at verified authority if its own head
generation is still current. Passing checks on already accepted claims retain the normal authority
upgrade/recovery rules. The model does not prove shell truth or that a worker stopped.

Recurrence counts canonical original-source identities, not repeated HTTP deliveries, relay
principals or an observation UUID generated afresh on each replay. Server-issued session keys are
namespaced once at issuance; copies retain that original key. One client can open several sessions, so this is a
recurrence policy, not Sybil resistance or proof of independent truth. Capture exposure at read time
against the served claim revision/status; later status changes must not erase the echo record.

Normalized exact content can establish agreement. Paraphrases require an affirmative typed agreement
judgment; conflict, timeout, disabled judge or no opinion leave them pending. S1 cannot accept without
recurrence. A held group preserves its alternatives. Acceptance records immutable support IDs,
revisions and source keys before retiring duplicates; supersession itself is not evidence revocation.
Rejection of support before acceptance invalidates the plan. Already-admitted historical support is
not a theorem of continued truth: a later contradiction requires an explicit rejection/correction or
an applicable check outcome on the current assertion.

Pending alternatives survive a head change and can participate in a newly planned admission when
at least one non-echo supporting observation was stamped against the current head generation. For
example, old port-2 candidate z can agree with new port-2 observation w after port 1 was admitted.
Old candidates alone cannot oscillate answers; freshness requires a new observation, not mutation
of old provenance. Relaying a canonical source already recorded in this group retains its earliest
observation generation and any echo marker, even under a new principal or command ID. The full current group revision
still fences every admission plan.

## Transaction and command contract

Each group has a monotonic revision, current-answer pointer and last-head-change generation. Proposals
remember their observation generation; check tokens remember the generation at check issuance.
Automatic check admission requires that issued generation, while recurrence requires at least one
current-generation observation. Both handle absent-to-present-to-absent head changes. A late valid
candidate check records evidence without authority/admission. All decision-relevant writes bump the group
revision in the same transaction. Each mutable claim projection has its own revision. Every command
carries `command_id`, authenticated actor and `expected_revision`. Database adapters commit claim
changes, group revision/head, audit events, dependent FTS/pointer writes and command receipt together.
External models and shell execution happen outside that transaction.

Idempotency is keyed by authenticated principal and command ID. A replay of the exact request returns
the original receipt, even after later state changes. Reuse with different input returns
`idempotency_conflict`. A group mismatch returns `stale` with no writes. A failed precondition leaves
no partial claim, audit or receipt. Transport adapters translate the model's typed `Refused.code` into
structured errors; success returns full IDs, committed revision and check token when applicable.

The adapters own authentication, route capabilities, canonical source/exposure stamps and group
selection. `Propose` contains no `source` or `echo` fields. The adapter supplies a separate `Stamp`
through `transition(..., stamp=Stamp(source=..., echo=...))`; portable traces put it in top-level
`trusted_stamp`, outside the action payload. The reference `Actor`, `Stamp` and maintenance-only
`agreement=agrees` represent trusted inputs, not client-selected capabilities or provenance. A retry
returns its original receipt/stamp even if a new exposure/session exists; it cannot gain a second
source. Its in-memory commit is a specification, not an implementation of SQL
isolation. Candidate-window queries must apply visibility/eligibility before their LIMIT.

Full IDs address mutations. Human-name discovery may return IDs, but an ambiguous target changes
nothing and returns the candidates. Reads may retain unambiguous handles. A later CLI/API slice will
expose live command schemas, structured results/errors and examples; this proposal introduces no
public HTTP command dialect. `v1.json` is a portable trace format, not that future wire schema.

## Delivery and migration

1. Merge this proposed contract, reference model, portable vectors, tests and decision amendments.
   The amendments are ratified by merge; CI owns revision publication. This PR changes no production
   behavior. The target runtime rules bind only at the adapter/store cutover described above.
2. Adapt PR #33's repository boundary to commands. Implement the same traces and richer shared
   conformance tests against the Worker and every local backend; keep its search/pointer improvements.
   Fix pre-limit visibility filtering and preserve current room, pin and operator handling on rebase.
3. Implement additive migrations: defaulted claim revisions, group heads/revisions, acceptance
   provenance, successor links, check attempts/results and unique command receipts/events. Legacy
   acceptance reasons stay `legacy_unknown`; do not manufacture a passing check or independent source.
   Existing checked rows become unconfirmed until rechecked. Existing rejected/superseded rows stay
   retired; old data cannot reliably distinguish manual rejection from a failed verifier.
   Schedule a full operator verify sweep as part of cutover and report its outcomes before relying
   on settled recall. This includes human corrections with configured verifiers. Timeouts/failures
   leave those groups without a usable answer; neither migration nor operator correction with a new
   verifier silently falls back to older knowledge. Show the unconfirmed group explicitly.
4. Resolve multiple accepted rows deterministically from the old authority/rank order, with an ID
   tie-break and an auditable migration event. Retain every row/handle. Validate a copied database
   before cutover; the source remains recoverable. No destructive in-place conversion.
5. Align every writer before enabling revision-only CAS: local/hosted remember, correct, reject,
   both MCP servers, distillation/seed/sweep/batch, arbitration/cron, late S1 rejection, filing, and
   local/hosted verification. PR #33's adapters share this boundary. A future Brief sync uses its own
   authenticated import command with ratifier provenance, never an ordinary MCP write.
6. Hosted verification must reject old ID/boolean-only callbacks with an upgrade-required error.
   Existing MCP tool names can stay, but descriptions/results must explain candidate corrections.
   Old local binaries cannot safely write an upgraded store: coordinate writer shutdown/upgrade or
   isolate a new migrated store path. Additive columns cannot fence code that ignores the protocol.
   Snapshot CAS may bridge a writer transition; it does not make an old unconditional writer safe.
7. PR #32's cooperation-validator fixes remain independent. Finish them after the contract-first
   slice; cooperation expiry, reported evidence and idempotency do not grant claim authority.

The current stage has no decay/widening/docs-promotion implementation. Event-clock decay can later
qualify live claims without adding approval states. The standing docs-promotion rule still requires a
merged owning-feature PR; that future retirement command records its pinned doc successor. The v1
reference covers claim-to-claim supersession only, not a completed docs-promotion workflow.

## Tests and optional proof

Run `python -m unittest discover -s tests -p test_claim_state_machine_spec.py -v`.
Tests cover terminality, explicit/recurrence acceptance, immutable assertion identity, failed support,
same-count group changes, stale attempts, failed/unknown checks, echo sources, replay receipts, all six
orders of two check results plus correction, and all 2,401 length-four sequences over seven commands.
Those finite schedules are bounded model exploration, not unbounded correctness or database proofs.

After real adapter conformance passes, a small Lean model could prove terminality, privilege
preservation and no state change on failed revision checks. Its assumptions would include trusted
actor/judgment inputs, faithful translation of this contract, SQL transaction semantics and adapter
conformance. It would not prove shell/model truth, authorization middleware, or the deployed system.
Effect-TS may organize Worker effects later; the pure contract has no Effect or engine dependency.

## Rex inputs: domain rules versus later presentation

The [Rex CLI](https://www.superlogical.com/rex/docs/automate/cli) and
[server API](https://www.superlogical.com/rex/docs/automate/server-api) motivate stable identifiers,
discoverable schemas and explicit domain operations. A claim here is a memory assertion, not a worker
lease. Task execution, review decisions and result readership are separate dimensions if future Kypp
records own them: completion is not approval; opening a result is not approval; a seen marker must be
keyed by viewer/result and bounded to the actual result revision. Seeing revision 7 cannot acknowledge
revision 8 or another viewer. Existing claim usage/exposure records remain observations, not sign-offs.

[OSC 7501](https://www.superlogical.com/rex/docs/build/program-status) is an optional terminal display
adapter: it guarantees capacity for 64 records, permits a cap of 256, evicts old records and has no
heartbeat. A later emitter can publish one root summary and bounded active/blocked/failed children,
clear successful children and retain totals, using coordinated run/attempt namespaces. Terminal state
never determines authoritative completion, review, lease ownership or liveness. Disconnection cannot
revoke a still-running worker; any future execution leases require atomic acquisition and fenced
renewal/completion at the durable mutation boundary. These UI/lease features add no claim states here.
