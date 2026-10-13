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

- Integrated `python -m unittest discover -s speccheck/tests -v`: 35 tests passed.
- `python -m speccheck bench`: all four splits passed their expectations.
- Live advisory check against `origin/main`: 8 conforms, 0 violates, 7 unsupported,
  3 unresolved. Exit 0 means the report was produced.
- Independent structural review found no high-confidence structural finding.
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
