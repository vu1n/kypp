---
id: claim-transition-contract
paths:
  - "specs/claim_state_machine/model.py"
  - "specs/claim_state_machine/v1.json"
  - "specs/claim_state_machine/cutover.py"
  - "specs/claim_state_machine/cutover-v1.json"
  - "kypp/claim_machine.py"
  - "kypp/claim_commands.py"
  - "worker/src/claim-machine.ts"
  - "worker/src/claim-commands.ts"
  - "worker/migrations/0010_claim_commands.sql"
  - "worker/migrations/0011_normalized_claim_commands.sql"
  - "tests/test_claim_state_machine_spec.py"
  - "tests/test_claim_commands.py"
  - "tests/test_claim_cutover_spec.py"
  - "tests/claim_trace_oracle.py"
  - "worker/test/claim-commands.test.ts"
  - "worker/test/claim-cutover.test.ts"
  - "worker/test/d1.ts"
  - "docs/claim-state-machine.md"
  - "docs/claim-cutover.md"
entry:
  - "kypp/claim_machine.py"
  - "kypp/claim_commands.py"
tests:
  - "tests/test_claim_state_machine_spec.py"
  - "tests/test_claim_commands.py"
  - "tests/test_claim_cutover_spec.py"
  - "worker/test/claim-commands.test.ts"
  - "worker/test/claim-cutover.test.ts"
---
# Claim transition contract and staged command adapters

A contract (#43) for lifecycle, acceptance, verification fencing and atomic commands.
The Python kernel and Worker port have isolated durable command adapters; live-store cutover is
still pending. `docs/claim-state-machine.md` names the completed checkpoint and remaining work.

## Gotchas
- The old reference path imports the Python kernel; it is not an independent oracle. Original
  portable vector expectations check outcomes; cross-language tests check the Worker port.
- `claim_command_*` projections and ledgers are isolated from legacy claims. No current route or
  cron calls them. Do not claim production conformance or partially switch a live store.
- Never persist the whole group's history in one value: D1 caps a string/row at 2,000,000 bytes.
  Group metadata stays small; claims and events are separate rows, and the existing receipt table
  is the sole durable retry authority. Mutation planning must not load event/receipt history.
- Normalized reads span queries: compare group revisions/metadata before and after to avoid mixed
  versions. Commit-time CAS remains mandatory even after a consistent read.
- A duplicate can commit between receipt miss and state load, not just at the write guard.
  Planning-stale recovery must consult the original receipt too; never re-plan at a newer revision.
- Canonicalize legacy global keys before migration head selection: local global rows can still
  carry their original project. Preserve that raw field and pointer document namespaces separately.
- Synthetic cutover inventory is not a migration. Unsorted project-null claims have no staged
  Group/File command yet; do not map them into one shared null group or silently discard them.
- Authorize current group reach before calling read/execute, including exact retries. An Actor
  fingerprint records trusted role input; it does not authenticate the caller or prove current reach.
- Migration 0011 upgrades only prior experimental command snapshots, retaining all claims/events
  and original receipts. Stop old experimental writers first; it does not migrate live memory_claims.
- Size-limit and rollback checks use local SQLite/D1 stand-ins, not the deployed D1 service. Large
  claim groups, individual payload bounds, query budgets and deployment latency still need validation.
- Pyturso DDL cannot run in `BEGIN CONCURRENT`; bootstrap idempotently before command transactions.
- Python `str.split` and JavaScript `\s` differ on NEL, C0 separators and BOM. Exact recurrence
  agreement uses the same explicit whitespace set; shared port tests cover it.
- Actor capabilities, the separate Stamp argument, and typed agreement are trusted adapter inputs,
  not client-selected permissions. Authenticate and authorize the exact group before invoking it.
- Retirement is terminal; check outcomes qualify live read eligibility. Viewing is not acceptance.
- Maintenance can reject unaccepted agent candidates, never accepted answers. Recurrence can reuse
  older alternatives alongside a current-generation observation. Check tokens fence their issuance
  generation; a blocked candidate pass records evidence without acquiring verified authority.
- Each new rule activates at complete adapter/store cutover. A contract-only merge
  does not require existing handlers to have already switched semantics.
- SQL interleavings and rollback tests cover all three local drivers and the D1 stand-in; they do
  not prove deployed D1 isolation, authentication, shell/model truth, or an all-writer cutover.
