# Claim cutover: bounded correctness checkpoint

Source baseline: `cadf80673b08d63939c3adde93a414851c90bfb1` (merged #45, after #32,
#33 and #44). This is a **disabled adapter checkpoint**, not an activated store or a
deployment runbook with completed gates. The governing contract remains
[claim-state-machine.md](claim-state-machine.md) and its rules.

The 2026-10-18 deep review was read in full (71,705 bytes; SHA-256
`5b6f0a743c9ac2f93f10c20b9348555f2a8e0baf85c38100d19052faf3b76331`). Its leads
were checked against source, not treated as authorization for a migration.

## Retry boundary

An exact retry must recover the original receipt at **both** races: another request
commits after the receipt miss but before the load/plan, or after planning but before
the transaction. The first race previously escaped through planning `stale` before
transaction error recovery. It is not enough that the final database has one claim:
the losing identical invocation must return the original receipt/revision/token.
Changed input with the same principal/command ID must return `idempotency_conflict`.
An unrelated stale command must leave every command table unchanged. A real storage
failure without a matching committed receipt must remain an error.

Authentication and current group reach precede **every** call into the staged API,
including historical reads/replays. `Actor` is a trusted in-process value, not an
authentication credential. The fingerprint includes its role, but that cannot prove
that the principal still has access. A wrapper test can establish the ordering
requirement; only future live middleware tests can establish production enforcement.

## Minimal placement decision before integration

Current evidence:

- Hosted `D1Store.claim` accepts `scope=project, project=null`; unknown origin remains
  immutable. `visible`, `get` and `liveClaims({filed:true})` restrict visibility and
  exclude unsorted claims from consolidation. See [store.ts](../worker/src/store.ts)
  and [migration 0003](../worker/migrations/0003_origin_projects.sql).
- `D1Store.file` is **first filing only**, not a generic relocation API. It changes
  placement and logs it without refreshing assertion time/source/origin. Pinned
  writers are restricted before filing by [defrag.ts](../worker/src/defrag.ts).
- Both staged `Group` validators require a project for project scope; neither kernel
  defines `File`/`Place`. Relaxing the validator would create one shared null-project
  arbitration group, with no session-isolation or two-domain fencing contract.

Recommended next contract slice (not implemented or adopted by this document):

1. Keep an unsorted assertion in a private intake lane with a stable identity and
   revision, separate from an accepted-answer group. Preserve access for its original
   authenticated user/session. No head, recurrence acceptance, or privileged trust is
   created by intake. Unknown origin still lands; the existing pinned-token exception
   still refuses out-of-pin origins rather than putting them in intake.
2. Add one `File` operation for first placement into a registered, currently permitted
   project. Its transaction fences the intake/claim revision **and** destination group
   revision, checks current pin/reach, moves the same full ID, writes audit/projections
   and the principal/command receipt together. Duplicate exact File returns that receipt;
   a competing different destination is stale, with no partial move.
3. Preserve content, verifier, origin, source/exposure, creation/assertion time and
   acceptance history. Filing is not fresh corroboration. It must not manufacture an
   observation of the destination's current head. A genuinely new observation/check
   supplies that evidence later. Arbitrary re-placement/widening stays out of this slice;
   if added, it must fence both old and new arbitration groups.

**Decision needed:** adopt that intake + first-File boundary, or specify a different
unsorted revision domain. This affects the command schema and persistence contract,
so the bounded bug fix does not silently choose or implement it. Operator/global placement is not evidence that
agents may pick scope, mint authority, or write outside a pin.

## Synthetic migration identity and preservation contract

[cutover.py](../specs/claim_state_machine/cutover.py) is a small, independent, **test-only
inventory**. It opens no database, imports neither kernel nor production rank/group
helpers, and does not emit runtime `State`, migrate rows, or activate a store. Its
hand-authored [portable cells](../specs/claim_state_machine/cutover-v1.json) and tests
freeze the following narrow expectations. This is source-exposed regression work,
not a blind independent holdout or production migration conformance.

- Canonicalize **before** selecting heads: global discards legacy placement project
  from its key; project/group retain it; user/room retain their distinct owner and
  audience, not a legacy project. Do not normalize subjects or IDs. Preserve every
  original field in the archive, including the discarded key component. Missing
  owners/unsupported key shapes are reported, never guessed. Unsorted remains unplaced.
- Only already accepted rows compete for a migrated head. Use legacy authority,
  confidence, source count and assertion recency, then greatest full ID in UTF-8 byte
  order for a deterministic tie. The last tie-break is a proposed concrete choice
  within the existing ID-tie-break requirement. Keep all candidates and retired rows;
  duplicate accepted losers acquire auditable supersession links to the selected head
  in the eventual migration. The inventory only lists those choices; it does not apply them.
- Retain legacy authority; never reconstruct an acceptance cause. Migrated acceptance
  provenance is `legacy_unknown`, including accepted history that loses the head.
  Checked rows start unconfirmed regardless of old authority/status. Retired rows stay
  terminal. Legacy rank's source count is **not** a grant of fresh recurrence evidence.
- Archive all raw claims, observations and pointer edges, not `live_claims()` or hydrated
  `Claim` objects. Preserve type/confidence, exact JSON metadata (including S1/custom
  fields), code refs, embeddings, validity fields, timestamps, agent/user, session,
  origin and original source IDs. Preserve non-claim pointers too. A pointer's
  `project` names its referenced document namespace, **not** the claim's placement.
- Two capture observations with distinct UUIDs but the same recorded session source
  are correlated, not counted as independent sources. Preserve both observations.
  Bare old session strings are not proven globally namespaced identities. No imported
  source gets a fresh generation or echo-free stamp; the inventory supplies **zero**
  recurrence-eligible sources. Missing/ambiguous identity stays unknown until trustworthy
  provenance exists. This does not invent a new runtime source identity by hashing a row.

The fixture uses raw SQL JSON strings where relevant, including a source-count case
whose string length would choose the wrong head. Its archive preserves byte-valued
embeddings. It does not exhaustively validate arbitrary legacy data, establish source
authenticity, or test an actual engine export/import. Those remain migration gates.

### Authoritative ownership at the future seam

| Data | Owner and required preservation |
|---|---|
| Lifecycle, head, authority, acceptance, check tokens/results, successors | Normalized command tables; no second writable lifecycle in legacy `memory_claims` |
| Full assertion ID/content/verifier, origin/source/exposure, original metadata | Immutable preserved record/sidecar keyed by full ID; never regenerate on replay or filing |
| Display type/confidence, timestamps, refs/embedding, S1/rotation bookkeeping | Explicit metadata projection; identify which changes are decision-relevant and fence those; do not overload assertion recency |
| FTS, lexical/semantic candidates, pointers | Derived projection with command transaction ownership; preserve standalone edges and apply authorization/eligibility before caps |
| Receipt request identity | Preserve existing fingerprint version; future type/refs/metadata request fields must participate in conflicts, not be written afterward |
| Read exposure | Capture served claim revision/status at read time; later retirement/promotion must not erase echo history |

This table specifies ownership constraints, not new storage tables. Metadata projection
layout and expanded transport request identity must be designed before all-writer cutover.

## Writer conversion-or-shutdown map

Every row below is **legacy/open**, not converted by this patch. Source paths identify
the live entry points; the disposition is required before activation of the same store.
Disable a path until its conversion exists rather than let it write the new store
through the old API. Disabling unsorted intake altogether is not an implementation of
the adopted unpinned-write contract.

| Surface / source | Required conversion or explicit shutdown |
|---|---|
| Local CLI `remember_main`, `correct_main`, `reject_main` — [shell.py](../kypp/shell.py) | Propose + optional explicit Accept, atomic Correct, full-ID Reject; retain metadata/projections and reject ambiguous handles before execution |
| Hosted CLI facade — [remote.py](../kypp/remote.py); privileged route — [operator.ts](../worker/src/operator.ts) | Same command semantics under the distinct operator secret; durable command IDs and structured errors; preserve `defrag_log` audit in transaction; #45's atomic correction is retained but not command cutover |
| Local MCP claim/aliases/correct/observe/consolidate — [mcp_server.py](../kypp/mcp_server.py) | Candidate-only Propose/replacement proposal, trusted source stamps; update descriptions/results; remove generic maintenance writes or route them through authorized maintenance commands; no verifier capability |
| Hosted repo/user MCP and `KYPP_CORRECT` branch — [mcp.ts](../worker/src/mcp.ts) | Candidate-only claim/correct, S1 and trusted source/exposure handling for both paths; current pin/group reach before any receipt/read disclosure |
| Hosted room claim/share — [mcp.ts](../worker/src/mcp.ts), [rooms.ts](../worker/src/rooms.ts), [store.ts](../worker/src/store.ts) | Preserve room owner isolation and explicit share; recheck mutable room link in committing transaction; room membership/link management remains an auth dependency, not claim authority |
| Wire/capture/distill — [wire.py](../kypp/wire.py), [distill.py](../kypp/distill.py) | Preserve original capture source across marker failure/retry; idempotent observation/import and Propose; no fresh source per retry; stop old capture hooks |
| Sweep/seed/batch — [autocapture.py](../kypp/autocapture.py), [seed.py](../kypp/seed.py), [batch_distill.py](../kypp/batch_distill.py) | Convert distill and post-batch consolidation together or disable jobs; `KYPP_URL` does not redirect these local-store writers |
| Local arbitration and library aliases — [arbiter.py](../kypp/arbiter.py), [repository.py](../kypp/repository.py), [store.py](../kypp/store.py) | Revision-fenced recurrence/supersession; remove `put/claim/set_status` escape hatches from upgraded write boundary, not only default constructors |
| Local/hosted verification — [verify.py](../kypp/verify.py), [remote.py](../kypp/remote.py), [operator.ts](../worker/src/operator.ts) | BeginCheck → external shell → CompleteCheck token; failure/inconclusive distinction; reject old ID/boolean callbacks with upgrade-required, never synthesize a token |
| Cron, immediate filing, consolidation — [index.ts](../worker/src/index.ts), [defrag.ts](../worker/src/defrag.ts), [consolidate.ts](../worker/src/consolidate.ts) | Stop scheduled/immediate legacy jobs until File and command plans exist; replace count/max-time plan guards with decision revisions |
| Late S1 rejection, judged/fileTried metadata — [defrag.ts](../worker/src/defrag.ts), [store.ts](../worker/src/store.ts) | Maintenance Reject only if still an unaccepted agent candidate at commit; fence awaited decisions; preserve S1 fields and classify rotation-only writes separately |
| Usage and pointer writes — [repository.py](../kypp/repository.py), [store.py](../kypp/store.py), [backends.py](../kypp/backends.py), [store.ts](../worker/src/store.ts) | Record revision-bound exposure and atomic claim-dependent FTS/pointers; retain standalone pointer indexing without authority mutation |
| Installed old binaries, in-flight callbacks, old experimental adapters | Stop/drain/upgrade or isolate the migrated store path; prove old processes cannot mutate it. New version flags unread by old code are not a fence |

Non-claim stores: room/pairing and project registry/parent changes affect reach and must
be revalidated by readers/commit boundaries, but are not Accept/Correct operations.
The S1 model endpoint has no memory writer; cooperation validators grant no claim
authority. `/sync`, docs retirement, cross-project widening and decay are future work,
not implemented writer coverage and not added by this checkpoint.

## Reader and projection conversion-or-shutdown map

| Surface / source | Required replacement before activation |
|---|---|
| Local recall/search/browse, semantic/LIKE/FTS/pointer windows — [store.py](../kypp/store.py), [backends.py](../kypp/backends.py) | Query command-owned read projection; authorization plus settled/check eligibility **before** LIMIT; preserve Unicode and semantic capability behavior |
| Local briefing/MCP recall-expand-briefing/CLI show — [view.py](../kypp/view.py), [mcp_server.py](../kypp/mcp_server.py), [shell.py](../kypp/shell.py) | Same projection, authorized historical full/prefix ID access distinct from settled recall; log served revision/status for exposure |
| Hosted lexical/pointer recall, `visible/roomVisible`, `get` — [store.ts](../worker/src/store.ts) | Preserve pin, user, room and unsorted reach before ranking/caps; settled accepted + applicable pass; historical retired rows remain expandable under current authorization |
| Hosted briefing shortcuts: orgs, recent project/user/room candidates — [mcp.ts](../worker/src/mcp.ts), [store.ts](../worker/src/store.ts) | Convert **every** shortcut, not only recall; label candidates/unconfirmed groups; don't silently substitute a weaker answer when the current checked head is unavailable |
| Hosted operator recall/show/briefing/verifiable — [operator.ts](../worker/src/operator.ts) | Authorized projection and current check issuance; no fallback to legacy booleans or a stale claim snapshot |
| FTS, embeddings, `code_pointers`, standalone context indexing | Inventory/rebuild/validate complete projections; atomic dependent writes, no dangling pointers or invisible acknowledged head; document namespace remains independent of placement |
| `claim_usages`, `shownCandidate`, local usage reports | Retain existing records as history; new exposure records bind served revision/status; never infer past exposure only from today's claim status |

No current reader consumes `claim_command_*`. A green command trace cannot show a
newly accepted answer to these live readers without this separate conversion.

## Activation prerequisites and bounded evidence

1. Settle the intake/File and metadata/transport seams above; keep kernels pure and
   normalized tables. Add independent expected outcomes for File races, no-write
   refusal, terminality and all new payload-identity fields. No Effect or Lean rewrite.
2. Convert or shut down **every** applicable writer/reader/projection row. Test current
   authorization before replay/history and mutable reach at commit, using live route
   middleware tests, not only trusted Actor construction. Reject old callbacks.
3. With separate authorization, obtain a consistent, recoverable copied database using
   its actual engine's supported snapshot method. Do not treat pyturso files as SQLite
   or copy a live WAL main file alone. Rehearse raw ID/content/metadata/origin retention,
   canonical global coalescing, audited head choices, checked-row unavailability,
   interruption/retry and reconstruction of projections. Report all unsupported rows.
4. Establish backend limits: group scans, largest correction, individual payload size,
   statement/query budget and atomic rollback. The Node SQLite harness is not live D1.
   A bounded disposable-D1 conformance run needs separate authorization; none ran here.
5. Demonstrate old writer shutdown, coordinated complete-store switch, and recovery.
   Before the first acknowledged new write, restoring the old snapshot may be possible.
   After it, stop and reconcile/forward-fix or use a tested lossless conversion; never
   silently lose the acknowledged correction by restoring an old snapshot.
6. On an authorized activation, run/report tokenized operator verification, including
   human checked assertions. Failed/inconclusive/not-run groups remain unavailable.
   Record exact artifact/store identity and representative route + raw-row histories.

The new tests cover the receipt/load/commit boundary and synthetic preservation. Existing
kernel traces cover accepted/candidate/terminal/check histories; generated Python/TS
trace agreement is **port equivalence**, not a second independent domain proof.
The synthetic inventory only describes a migration plan; no copied-store or production
migration has been performed. The first filing test initially failed on Node SQLite's
null-prototype row comparison; fixing the assertion normalization was a harness fix,
not a product defect. Retain that distinction from the genuine red retry regression.

Checkpoint validation (local worktree based on `cadf806`, Python 3.12.14 / Node 24.19):

| Check | Observed result |
|---|---|
| Initial receipt-miss race tests, before fix | Python: 9 failures/errors across three drivers; Worker: 3 failures |
| Full `python -m unittest discover -s tests -v` | 201 passed, including 10 synthetic inventory cases and five-revision read-exhaustion schedules |
| Worker `npm test` / `npm run typecheck` | 304 passed / typecheck passed |
| `git diff --check`, `uv lock --check` | Passed |

The replay receipt fingerprint was checked against independently hand-serialized JSON,
not generated by the Python kernel oracle. No installed-wheel, Wrangler service
or live-D1 smoke was repeated for this slice; Python verifier/MCP self-tests did pass.

Executable transition/SQL interleaving tests remain the priority. A later small Lean
proof could establish terminality/authority consistency for an explicitly modeled
transition relation, assuming trusted actor/stamp inputs and faithful SQL refinement.
It would not prove live auth, source independence, projection/migration completeness,
shell truth, backend limits or rollout safety. No theorem or new proof toolchain is
needed to close this concrete race.
