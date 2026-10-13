# Source inventory completeness

The advisory checker must read every selected source file before reporting on the
tree. A selected file that cannot be read or decoded is a broken check, not an
empty source file or evidence of conformance.

## Reproduced gaps

On published main `0e80abc1bcafa1aed1ff312bc2827126a9290011`, a selected Python
file containing a broken reference followed by non-UTF-8 bytes appeared in the
path inventory but disappeared from loaded content. The reference rule returned
`conforms` without examining that file. In real committed Git fixtures, a broken
reference inside filenames containing a tab, newline or non-ASCII character was
also lost. A space-containing filename was a passing control.

The new loader accepts a complete selected UTF-8 regular-file inventory or raises
`GitError`. The CLI already distinguishes a loading failure from an advisory
finding: a failed check exits nonzero before producing report or cache output;
a produced report still exits zero, including when its verdicts violate.

## Scope and boundaries

Content selection remains `.brief/`, `kypp/`, `worker/src/`,
`worker/migrations/`, `docs/`, and `.github/workflows/`. Other paths remain in
the feature inventory but their content is outside the loader's selected scope.
This does not extend the checker to additional code paths or prove global absence.
Excluded worktree paths receive an existence check only; a symlinked asset
directory does not trigger the selected-source restriction or a content read.

Git paths must be preserved literally rather than split on newlines or interpreted
from Git's display quoting. Selected symlinks and other non-regular entries are
rejected in worktree and committed modes. External targets are not source evidence.
Worktree deletion is a valid change: an absent file is removed from both returned
content and paths. Read, decoding and type failures must not be mistaken for deletion.

This is a local CLI source-loading boundary. It does not introduce source execution,
new dependencies, runtime Kypp behavior, atomic snapshots of concurrently edited
worktrees, or decision ratification. Commit-mode checks remain the reproducible mode
for an immutable tree. Existing report files are not updated when loading fails.
Worktree traversal uses POSIX directory descriptors and no-follow flags; this
increment was validated on Linux. It does not claim Windows support.

## Evaluation and integration

An independent end-to-end corpus freezes expectations before scoring the settled
implementation. Its first results and subsequent exposure are recorded in
[source evaluation](spec-check-source-evaluation.md). Loader tests exercise real
Git repositories and operating-system file shapes; review mutation checks run in
an isolated scratch lane.
The original corpus had invalid sentinel/fault assumptions and was repaired as
regression evidence. A separate [frozen set](spec-check-source-holdout.md) produced
eleven valid first-observation successes and one invalid scope expectation.
These small synthetic sets do not estimate a population error rate.

## Validation

- Full checker suite: 72 unittest methods passed, including 26 named corpus subtests.
- All existing tuning, regression, previously exposed holdout and smoke benchmark splits passed.
- Fifteen meaningful mutants were killed. Initial survivors prompted tests for
  replacement between stat/open, duplicate unmerged Git index entries, and descriptor cleanup.
- Independent structural review found no high-confidence structural blocker.
- Required Brief check and pin passed; doctor retained only the three pre-existing stale pins.

Exact pushed-head correctness/security review and workflow conclusions are recorded
in the draft PR, since they follow this local validation.
The first correctness review identified strict traversal applied to excluded
symlinked asset directories. Its red-first regression now passes after keeping
those paths in the existence-only inventory; the settled fix receives a fresh review.
Another fresh review identified excluded descendants after a directory is replaced
by a file. Those descendants now count as absent, with a red-first regression;
selected-source traversal still rejects nonregular parents.

This branch starts directly from main. Open PR25 extends semantic rule coverage;
open PR26 supplies SQL migration evidence. Neither is included or required here.
Their review branches, the separate substrate amendments and SIGNOFF proposals,
and runtime code are untouched. No merge or deployment is authorized.
