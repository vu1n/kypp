# Independent source-inventory evaluation

## Original frozen inputs and labels

The independent INV-002 lane authored
`speccheck/tests/test_source_inventory_corpus.py::CASES` from PLAN.md's selected
source contract and the public CLI, report model, and fixture interfaces. The
source-loading implementation, INV-001 worktree, implementation tests, PR25 and
PR26 were not read. No corpus case has been executed or scored before this freeze.
Only Python syntax compilation and required Brief governance checks were performed.

The original freeze is the commit introducing this document and the corpus together.
Record that immutable commit and both file SHA256 values in the integration evidence;
do not reinterpret later corpus revisions as the original frozen inputs.

There are **26 cases**: **6 clean-report controls**, **11 retained-reference finding
cases**, and **9 explicit-loading-failure cases**. These are separate label
denominators, not 26 independent real-world samples. First scoring is pending the
parent integration lane after INV-001 settles. No pass rate, failure count, mutant
result, or correctness conclusion is claimed here.

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

Root-run CI can read chmod-protected files. The inaccessible-file case therefore
injects `PermissionError` at Python's `io.open` boundary in the CLI subprocess,
with only the exact selected path affected. It still uses a real repository and
CLI but is a controlled read-boundary fault, not an OS permissions integration
test. Symlinks target an external sentinel file; rejection proves an explicit
failure but does not by itself instrument or prove that no external read occurred.
Committed read failures beyond decoding are not separately injected. No verify
argument case is included because references provide direct content-retention
counterexamples without importing additional evaluator interfaces.

Cases share fixture material and are intentionally bounded; they measure this
contract's behaviors and do not estimate broad parser accuracy. Mutation scoring,
implementation exposure, first observed results, and any post-freeze changes must
be recorded separately by the integration lane.
