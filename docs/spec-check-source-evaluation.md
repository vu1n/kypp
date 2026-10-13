# Independent source-inventory evaluation

## Original frozen inputs and labels

The independent INV-002 lane authored
`speccheck/tests/test_source_inventory_corpus.py::CASES` from PLAN.md's selected
source contract and the public CLI, report model, and fixture interfaces. The
source-loading implementation, INV-001 worktree, implementation tests, PR25 and
PR26 were not read. No corpus case had been executed or scored before the original freeze.
Only Python syntax compilation and required Brief governance checks were performed.

The original freeze is commit `999b6a2b6aa509b4982fa6fb47cf5cc9fcb72b46`.
Its corpus SHA256 is
`df079265ff442db329dbd6126ed7748fec96c1a06dbcdc7434b2309a451c10b7`;
its documentation SHA256 is
`36c5cd8b0a02552c6a24e8ed886136743d9634a1428004579857bf117d56a346`.
Later repairs are exposed regression inputs, not the original frozen inputs.

There are **26 cases**: **6 clean-report controls**, **11 retained-reference finding
cases**, and **9 explicit-loading-failure cases**. These are separate label
denominators, not 26 independent real-world samples. The parent first exercised the original case bodies at integrated revision
`bb98fa3c7f4d0aa1eb899d03c42305a4536887ea`, removing only the pytest import and
decorator in memory because repository CI uses unittest and pytest is undeclared.
The first observed result was **15/26 passing**. That figure does not validate
reference accuracy or the permission-denial harness: 14 valid control/failure
cases passed, 11 reference cases used a malformed reference sentinel, and one
nominal denial pass was invalid.

## Expected public behavior

Each case creates a real temporary Git repository containing `clean_tree()` and
uses the CLI in a subprocess with an explicit baseline, report path, JSON path,
and cache path. Committed cases pass `--head`; worktree cases omit it. Reports are
advisory and exit zero even with findings. JSON follows `Report.to_dict()` and
contains a `doc-refs` verdict. A selected broken reference must produce a violation
whose counterexample includes the exact literal source path. Successful clean
controls require conforming reference coverage.

Selected invalid UTF-8, inaccessible source, symlinks and nonregular tracked
replacements must produce a nonzero CLI result, an error diagnostic, no report on
stdout, no new output files and no new or changed cache files. Two failure cases
prepopulate report outputs and cache content and require preservation. The FIFO
case has a bounded subprocess timeout: a blocking load is an evaluation failure.

The unusual filenames include spaces, tabs, newlines, non-ASCII characters and
literal quotes in both committed and worktree modes. Reference tokens are split
in the corpus source so the repository's own scanner does not interpret corpus
sentinels as real references. The encoding cases place invalid bytes before and
after useful source, truncate a multibyte sequence, and place valid UTF-8 boundary
code points beside a reference sentinel. Deliberate worktree deletion removes a
tracked source with an unusual path. Ignored invalid source, excluded binary
content and an excluded symlink are negative controls.

## Assumptions and limitations

The corpus assumes `.py` under `kypp/` is selected, binary extensions under
`assets/` are excluded, ignored untracked files are excluded, and a deleted
worktree source is absent from reference evaluation. Baseline decoding failure
must also abort, even when head loading succeeds. The fixture's reference rule
identifier is assumed to be `doc-refs`, as named in PLAN.md.

Root-run CI can read chmod-protected files. The repaired inaccessible-file case
injects a narrowly targeted `PermissionError` into Python path-open and OS
open/read boundaries. Descriptor targets are identified through Linux
`/proc/self/fd`; unrelated integer descriptors pass through safely. The CLI must
report the specific `corpus-selected-source-permission-denied` sentinel, so an
unrelated harness exception cannot satisfy the case. This is a controlled read
fault, not a real OS permissions integration test, and assumes Linux `/proc` and
Python-mediated file access. A reader bypassing these boundaries would require a
new fault harness, not relabeling a success as denial evidence.

Symlinks target an external sentinel file; rejection proves an explicit
failure but does not by itself instrument or prove that no external read occurred.
Committed read failures beyond decoding are not separately injected. No verify
argument case is included because references provide direct content-retention
counterexamples without importing additional evaluator interfaces.

Cases share fixture material and are intentionally bounded; they measure this
contract's behaviors and do not estimate broad parser accuracy. Mutation scoring,
implementation exposure, first observed results, and any post-freeze changes must
be recorded separately by the integration lane.

## Exposed repairs after first observation

The parent disclosed all first-observed outcomes before these repairs. The corpus
author then read only the public `speccheck/textscan.py` grammar, which confirms
numeric revisions contain one to four digits without a `v` prefix. The sentinel
was repaired from `@v0042` to `@0042`; this is an input change affecting the 11
reference-finding cases and invalid-byte/control source payloads that also contain
the sentinel. Case rows, names, expected labels and denominators remain unchanged.

The original denial wrapper passed integer subprocess pipe descriptors into
`os.fspath`, producing the audited diagnostic
`speccheck: expected str, bytes or os.PathLike object, not int`. Thus its apparent
pass did not exercise permission denial. Parent audit evidence is at
`/workspace/scratch/kypp-source-denial-audit.json`. The repaired wrappers handle
integer descriptors and require the actual injected denial diagnostic.

The pytest parametrization was replaced by standard-library unittest discovery:
`SourceInventoryCorpusTests.test_frozen_cases` runs 26 named subtests through
standalone `run_case`. These edits are regression repairs after exposure. The
author has not executed or rescored the repaired corpus and has still not read
the source-loader implementation or its tests. Only syntax compilation and
required Brief checks precede the repair commit. Parent scoring of the repaired
corpus remains pending; no blind-accuracy or mutant result is claimed.
