# Worker signature checker continuation

The checker now abstains when a Worker TOOLS initializer contains an entry it cannot
enumerate and retains a literal `verify` counterexample when another schema is opaque.
PR23's merge is the verified base: `92dc50c05d0688b6644e89c6b0a6343b29d67713`.

## Observable evidence

These cases were reproduced against the old scanner before implementation. The
new tests exercise the real TypeScript tree-sitter parser and advisory evaluator.

| Input shape | Before | After |
| --- | --- | --- |
| A clean literal tool followed by `externalTool` | conforms; entry skipped | unresolved; enumeration gap |
| A literal `verify` tool followed by `buildSchema()` | unresolved; counterexample lost | violates; gap retained in coverage |

The signature rule still uses the ratified baseline bullet at
`doc://kypp/verify-operator-only@0002#operator-only-verify`. Head proposals and
SIGNOFF prose do not become the rules. Writer completeness, POST /sync, behavioural
proofs, and SCIP indexing are outside this increment.

## Frozen evaluation

The independent corpus was committed at `91eb908c83e6cce8ffbb62f0a7ef013657ecd15a`
before its author inspected any implementation or scored a case. First evaluation
ran against integrated `1019dd07991fb451bca996216a0344f6ffb10255`, after SC-001 settled.
All expected labels passed; no implementation was tuned from those results.

| Split | Cases / judged rows | False conformance | Abstention | Coverage | Smoke false flags |
| --- | ---: | ---: | ---: | ---: | ---: |
| tuning | 13 / 13 | 0 | 0.462 | 0.538 | — |
| regression | 13 / 13 | 0 | 0.154 | 0.846 | — |
| fresh holdout at first evaluation | 9 / 9 | 0 | 0.333 | 0.667 | — |
| smoke only | 30 / 0 | — | — | — | 0 |

The nine cases are now exposed: later runs are regression confirmation rather than
a fresh independent accuracy estimate. Nine synthetic cases do not establish a
population error rate. See [corpus provenance](spec-check-evaluation.md) for the
freeze hash, preserved PR23 cases, metric denominators, and disclosure rules.

## Validation and review

- Final `python -m unittest discover -s speccheck/tests -v`: 40 tests passed.
- `python -m speccheck bench`: all four splits passed their expectations.
- Live advisory check against `origin/main`: 8 conforms, 0 violates, 7 unsupported,
  3 unresolved. Exit 0 means the report was produced.
- Independent structural review found no high-confidence structural finding.
- The first pushed-head correctness review found an overbroad reference-use
  whitelist. A schema passed through an `inputSchema` pair outside TOOLS now
  abstains; its regression went red before the fix. The parser cache version was
  advanced so earlier whitelist facts cannot survive the correction. Dependency
  version invalidation is tested separately for runtime and grammar changes.
- A fresh review also identified shorthand-property escapes. Shorthand references
  and uncertain escaped identifier spellings now abstain, with red-first regressions
  for both; previous parser cache namespaces are rejected.
  Escaped schema binding names also abstain, since their plain aliases cannot be
  resolved soundly by raw spelling.
- Exact pushed-head correctness review, separate static security review, mutant
  results, and GitHub CI conclusions are recorded in the draft PR.

## Operation and remaining limits

From this checkout, install `.[speccheck]`, or use
`uv run --extra speccheck python -m speccheck check`. CI installs these extras,
runs the checker tests and benchmark, then uploads its advisory report. Runtime
and grammar versions are part of the syntax cache identity; missing dependencies
return an explicit gap without persisting it. Old scanner facts are not reused.

Worker evidence is an inventory of literal initializer keys. It does not prove
runtime immutability or global absence. Ambiguous enclosing fields and unsupported
schemas abstain; a counterexample is emitted only from retained literal evidence.
Prose invariants, complete authority writers, and runtime verify/schema behaviour
remain unsupported or unresolved. The existing authority-rank rule still excludes
the Worker's rank implementation; the authority-body scan is unchanged.

`brief doctor` has three existing stale memory-scope pins on main:
`kypp/arbiter.py:90`, `kypp/arbiter.py:122`, and `kypp/distill.py:366`. This increment
does not change those governed runtime paths. No new active decision was edited or
ratified. The PR remains draft pending the user's separate merge decision.

## Assurance model

A recognized counterexample can defeat a claim. An unrecognized construction cannot support one.
Semantic scanners do not emit a blanket pass.

Report outcomes stay separate. They are not one green badge.

| Outcome | Meaning |
| --- | --- |
| violation detected | a counterexample is established in the rule's scope |
| candidate finding | a suspicious construction whose target or execution is unproved |
| no counterexample found | the scan found none. This is not assurance |
| unresolved / unsupported | the check does not answer |
| bounded proposition | a closed-world sentence over an enumerated set, worded as that sentence |
| test passed | a named test, the candidate SHA, and the environment. A skip is not a pass |
| guard verified | unused |

Bounded propositions in this tree: every examined `doc://kypp` ref in the enumerated file set resolves; each `aliases.yaml` latest entry equals the highest version file; every SIGNOFF line names an anchor present in the baseline spec; every feature-map glob matches a file in the enumerated head set; the fully parsed `TOOLS` initializer declares no `verify` property.

`verified-authority-writer` reads `authority-order@0006`. `MemoryStore.mark_verified` is the only local path that grants verified. `POST /sync` is a separate import and is not built, so it is not an allowed writer and ratifying it is not evidence. A proved write of `authority='verified'` outside `mark_verified` violates. An unclassified write stays unresolved. A scan with neither is `no_counterexample`, not assurance.

SQL has no allow-list parser. `?` and other untraced bindings are unknown. A protected `DELETE` next to an opaque sibling is still a violation. Upsert, an early `compareRank` return, and a TypeScript concatenation never produce a positive assurance result.

## Benchmark

The split inventory is `tuning`, `regression`, `gap-holdout`, and `smoke`, defined once in `speccheck/bench.py`. An empty requested split fails. Exposed cases, including the nine worker signature cases, are regression. Known-violation detection counts only the scored cases that expect a violation. False violations are `violates` on a row that did not expect one. Noisy candidate findings are `candidate` on a row that expected neither a violation nor a candidate. The two are reported separately.

| Split | Cases | Known-violation detection | Unresolved rate | False violations | Noisy candidates | Mutation detection |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tuning | 18 | 1.000 | 0.278 | 0.000 | 0.000 | n/a |
| regression | 22 | 1.000 | 0.182 | 0.000 | 0.000 | 1.000 |
| gap-holdout | 49 | 1.000 | 0.367 | 0.000 | 0.000 | n/a |
| smoke | 30 | — | — | 0.000 | 0.000 | — |

## Runtime contracts

`pyproject.toml` depends on `mcp>=1.30,<2`. The server is still FastMCP; an MCP v2 migration is separate. CI installs that wheel in a clean virtualenv and constructs the server with `python -I`, then prints `kypp.__file__` and `kypp.mcp_server.__file__`. Those paths have to sit under the venv, which is the proof the installed wheel was imported. Importing `kypp.mcp_server` does not import the SDK. `tests/test_mcp_construct.py` is that construction check. `tests/test_mcp_contract.py` lists every tool, rejects a `verify` or `authority` schema property, calls every tool with ordinary arguments, then calls them again with `verify` and `authority='verified'` or `'human'`. Content may contain the word verify. A store row that kept a verifier fails the same assertion. `worker/test/mcp-contract.test.ts` does this through `handleMcp` `tools/list` and `tools/call` on the `node:sqlite` D1 double. The collector reads `SELECT *` and every `PRAGMA table_info` column. A tools/call mutation that writes a verifier, including `""`, fails that same collector. `dispatch` does not re-check the schema, so the test reads the rows.

`tests/test_verify_timeout.py` asserts that `run_check("sleep 30", timeout=0.4)` returns failure and a timeout message within five seconds. It does not prove that every descendant is dead, that a failed spawn is handled, or that a sweep continues.

`tests/test_rank_oracle.py` and `worker/test/rank-oracle.test.ts` compare survivor selection with an oracle from the spec. Each strict authority pair (`human` over `verified`, `human` over `agent`, `verified` over `agent`) wins in either argument order when the lower authority is better on one secondary key at a time: confidence, status, evidence, or recency. A confidence-only early return does not match the oracle.

Contract tests are CI failures. `speccheck check` stays advisory and exits 0.

Tested locally: Python 3.12.3, mcp 1.30.0, pyturso 0.8.3, Node 22.21.0. Node 22.14's `node:sqlite` has no FTS5, so the worker job pins 22.21.

## Backends, and why triggers wait

The Python store is Turso via pyturso: MVCC and `BEGIN CONCURRENT`. The worker test double is `node:sqlite`. Production worker storage is D1. Trigger migrations are deferred. `store.py` splits `SCHEMA` on `;`, so a trigger body that contains semicolons would be executed as separate statements. `INSERT OR REPLACE` bypasses delete triggers unless `recursive_triggers` is on, and Turso does not support that pragma.

## Historical fail-closed notes

The table below is the previous allow-list experiment. It is not the current rule. A rule no longer conforms because a statement matched a grammar.

| Rule | Conforms only when |
| --- | --- |
| `mcp-no-verify-argument` | Both `kypp/mcp_server.py` and `worker/src/mcp.ts` parse, every tool is enumerated, and no parameter or literal schema key is `verify`. A model is closed only when every `extra` / `model_config` is absent or a literal `forbid` or `ignore`. `*args`, bare `**kwargs`, an open model, a spread, or an unresolved `add_tool` target is unresolved. |
| `mcp-no-human-or-verified-authority` | The same covered tool surface, and no literal authority value is `human` or `verified`. A non-literal authority expression is unresolved. |
| `human-authority-writers` | Never conforms. The `POST /sync` writer is named by the spec and is not in the tree. |
| `authority-rank-order` | Python `Authority`, `AUTHORITY_RANK`, `_rank`, and `_survivor` match the proved order, and `compareRank` is exactly `rank(a)` / `rank(b)` bindings, one index loop whose discriminators are `ra[i] - rb[i]`, then `return 0`, and `survivor` keeps the higher rank. Any other control flow is unresolved. A proved negation still violates. |
| `schema-add-column` | Every `_MIGRATIONS` string is one fully parsed `ALTER TABLE … ADD COLUMN` with optional `DEFAULT` and optional `NULL` / `NOT NULL`. |
| `schema-idempotence` | Never conforms. A version table violates; the absence of one does not prove the duplicate-column behaviour. |
| `verify-timeout-behaviour` | Never conforms. Timeout handling is runtime behaviour. |
| `no-delete-claims` | Every statement that names `memory_claims` or `observations` is fully parsed as plain `DELETE`, `UPDATE SET`, `INSERT` (columns plus `VALUES` or `SELECT`), `SELECT`, `ALTER TABLE ADD COLUMN`, or `CREATE TABLE` / `INDEX`, with no trailing hole. Constant `+` and templates are folded first in Python and TypeScript. |
| `verified-authority-writer` | Same allow-list for statements that name `authority`. A literal `verified` in an assignment to that column violates, including inside `ON CONFLICT` and `REPLACE`, except the `CASE` inside `MemoryStore.mark_verified`. `WHERE authority = 'verified'` is not an assignment. `claim(..., authority=<constant other than verified>)` conforms; a non-constant argument is unresolved. |
| `no-grader-verbatim` | `kypp/distill.py` exists and every `ClaimDraft` subject and content is proved free of criterion `name` and `feedback`. Zero drafts, or any unknown expression, is unresolved. One proved leak violates even if another draft is unknown. |
| `doc-refs` | Every examined `doc://kypp` ref resolves to an id, a version that exists, and an anchor in that version. |
| `aliases` | `aliases.yaml` is present when versioned docs exist, and each `latest` is the highest version file. |
| `signoff-anchors` | `.brief/SIGNOFF` exists and every line names an anchor that exists in the baseline spec. A missing file skips the rule. The reason text is not evidence. |
| `feature-paths` | `.brief/docs/features.md` exists and every `paths:` glob matches at least one head path. A missing file skips the rule. |

Anything outside those shapes is unresolved unless a counterexample is proved. Upsert, `RETURNING`, a CTE, a trigger, and `REPLACE` are unresolved for the SQL rules unless they contain a protected `DELETE` or an assignment of `'verified'` to `authority`.

### Mutants tried against a false conforms

Each of these was aimed at the old default-pass behaviour. The expected verdict is the one the fail-closed rule now gives.

| Attempt | Result |
| --- | --- |
| `INSERT … VALUES ('s','agent') ON CONFLICT DO UPDATE SET authority='verified'` | violates `verified-authority-writer` |
| Same upsert with `SET authority='agent'` | unresolved, not conforms |
| `INSERT … VALUES ('s','agent') RETURNING id` | unresolved |
| `REPLACE INTO memory_claims(authority) VALUES ('verified')` | violates |
| `UPDATE … SET status='accepted' WHERE authority='verified'` | not a writer |
| `compareRank` binds `rank(a)` / `rank(b)`, then `return a.confidence - b.confidence`, and leaves the loop unreachable | unresolved `authority-rank-order` |
| The proved loop plus an extra `if (ra[i] == rb[i]) return 0` | unresolved |
| Worker `"DELETE FROM " + "memory_claims"` | violates `no-delete-claims` |
| `CREATE TRIGGER … BEGIN DELETE FROM memory_claims; END` | violates |
| `WITH gone AS (SELECT 1) DELETE FROM observations` | violates |
| `WITH c AS (SELECT id FROM memory_claims) SELECT id FROM c` | no counterexample; not a delete finding |
| `ALTER TABLE … ADD COLUMN user TEXT; DROP TABLE observations` | violates `schema-add-column` |
| `ADD COLUMN user TEXT CHECK (length(user) > 0)` | unresolved |
| `model_config = {**base, "extra": "forbid"}` | unresolved `mcp-no-verify-argument` |
| `ClaimDraft(..., obscure(crit["name"]), ...)` | unresolved `no-grader-verbatim` |

On the live tree, `no-delete-claims` is unresolved. The `UPDATE memory_claims … AND ${UNSORTED}` writes in `worker/src/store.ts` end in a hole, so the scan cannot see whether another statement follows. That hole is coverage, not a delete candidate. Schema, migrations, and ordinary inserts that name `memory_claims` are not findings. `SELECT replace(...)` is not a delete. `verified-authority-writer` reads `authority-order@0006` and stays unresolved on the live tree because some authority writes are still unclassified. A proved extra write would violate.
