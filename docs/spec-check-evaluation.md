# Spec-check evaluation corpus

SC-002 independently authored nine fresh Worker MCP signature cases against the
ratified invariant: MCP tools never accept a `verify` argument. Source decision:
`.brief/docs/verify-operator-only/versions/v0002.md`, anchor `operator-only-verify`.
Starting checkout: `319f6d931e5e1f020781987d7497171e734696e6`.

## Provenance and freeze

The author read repository instructions, SC-002, the ratified verify decision,
fixture/model/benchmark/CLI interfaces and feature-map Gotchas. The author did not
read `speccheck/syntax.py`, `speccheck/evaluate.py`, the implementation worktree,
the new parser, or another agent's implementation. Fixture imports load evaluator
constants; its source was not opened or inspected. Fresh expected labels were
assigned before evaluation. No fresh holdout case was evaluated by this author.
Default split selection constructs cases but mocks scoring for holdout.

Fresh cases include differing literal syntax, partial enumeration, schema
indirection, uncertain constructs, comments/strings as source data rather than
governance, and clean controls. Conformance requires covered enumeration of bounded
literal signatures, not runtime schema evaluation or global absence. A direct
covered counterexample remains a violation despite unrelated coverage gaps.

Freeze commit: recorded in the following documentation commit after freezing.
Fresh canonical SHA-256: `2412362ea7918456293bfeee26dfc3cb99d65f7bce1b77b2cd9baef0f4573c31`.

All 13 exposed PR23 holdout inputs are now regression data. Their old `holdout_`
IDs remain as provenance, not an independence claim. Baseline/head inputs,
descriptions and expectations are unchanged; comparison with the pre-edit git
version matched all 13 cases. Legacy canonical SHA-256: `68324b1747ee2ca4290f8d758bde3ac177a6ff99d46ff0af8668c6d36f567020`.
Hashes serialize all dataclass fields except `split`, recursively sort sets, use
sorted JSON keys and compact separators, preserve case order, then hash UTF-8.

## Reporting and disclosure

Benchmark defaults and `--split all` report tuning, regression, holdout and smoke
separately. `--split regression` selects exposed cases. False-conformance is returned
conformance among expected nonconformance rows. Abstention includes unresolved,
unsupported and missing verdict rows. Coverage is returned conformance or violation
among all expected rows. Missing verdicts still fail expectations and stay in every
applicable denominator. Zero false conformance alone does not establish useful
coverage. Smoke reports false-flag rate only and is not an accuracy metric.

Before freezing, `python -m unittest speccheck.tests.test_bench -v` passed six tests.
Actual evaluation selected tuning, regression and smoke only; default holdout
selection used a scoring mock. Missing-verdict denominator tests and legacy
preservation comparison passed. Brief features was consulted before edits; Brief
check and pin are required before both commits.

The parent owns post-freeze evaluation after implementation settles and must
record actual per-split results and limits. Any exposure of inputs or checker
outcomes must be disclosed with the evaluated revision. If results lead to
implementation fixes, move affected cases to regression and freeze newly
independent cases before claiming holdout performance. Independent corpus freeze
and split/metric checks are complete; implementation acceptance is pending parent
evaluation. No labels were tuned to checker outcomes.
