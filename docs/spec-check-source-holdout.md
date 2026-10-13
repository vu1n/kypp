# Independent source inventory holdout

INV-004 freezes twelve fresh end-to-end cases in `speccheck/tests/test_source_inventory_holdout.py`. The author read AGENTS.md, PLAN.md, the public CLI, report model, fixtures, reference scanner, and the feature map. The author did not inspect the source loader, its implementation tests, the existing corpus, or other implementation/corpus worktrees. No holdout cases were executed before this freeze.

The sentinel uses the public reference grammar: project `kypp`, document `holdout-missing`, revision `0042`, anchor `retained`. The document intentionally does not exist. Within reference-scanning scope, its presence must produce a doc-reference advisory violation with exit zero and visible literal path and reference evidence. The token is constructed from separate strings in the test source to prevent repository scans from treating fixture data as a real reference.

The frozen denominators are six content-retention violations, four explicit source-loading errors, and two clean controls, for twelve cases total. Five cases use committed head mode; seven use worktree mode. The six retention paths cover every selected prefix and include spaces, a tab, a newline, Unicode, literal glob metacharacters, and a quote/backslash. Error cases exercise invalid UTF-8 and symlinks in both head modes. The clean cases cover intentional worktree deletion and invalid/out-of-scope files, including a prefix lookalike and a dangling symlink.

Errors must return nonzero with an explicit diagnostic and leave no Markdown report, JSON report, or cache entries. Symlink cases point outside the temporary repository. This holdout does not inject permission faults, measure performance, cover Git submodules, or claim exhaustive unreadable-file coverage. The original first observation and its scope correction are recorded below.

Run after freeze against the settled implementation:

```sh
python -m unittest discover -s speccheck/tests -p test_source_inventory_holdout.py -v
```

The freeze commit and SHA-256 of the input file are supplied in the author handoff. Record the first score separately without changing these frozen inputs or labels; any later repair must preserve this original freeze and disclose exposure.

## Exposed scope correction

Original freeze: `16f548fa247988821ab80f90f9afff1524615f78`. Original test-file SHA-256: `d43bac402fd84ab47962c7452e31fa3803dce04e54c192daee8609f57cf08b0f`. The original six/four/two label distribution above describes that durable freeze, not the corrected labels.

The parent reported first scoring at integrated commit `fc1f877eacd45727353bbeb5ecb5299d8b408a6e`: eleven of twelve cases passed. Only `worktree-brief-quote` failed, with a conforming doc-reference verdict covering zero references. All five valid retained-reference cases, four loading-error cases, and two clean controls passed. The one invalid expected outcome is excluded from the accuracy denominator: the valid first observation was eleven of eleven, with one mislabeled case.

After that observation, the author independently inspected `speccheck/structural.py::_scanned_pairs` and the public doc-reference verdict scope. Both specify baseline `.brief/` references plus head code prefixes; head-only `.brief/notes/` is excluded. Source-loader selection and reference scanning have different scopes. The original expectation therefore could not prove content retention at this path.

The correction changes only this case's expected label to clean and asserts zero examined references, ensuring its head-only note is not promoted into baseline evidence. Its literal path, content, mode, and operation remain unchanged, as do the other eleven cases' inputs and labels. Corrected denominators are five retained-reference violations, four loading errors, and three clean controls. This case now tests semantic scope exclusion, not `.brief/` loader retention.

The original freeze remains in Git history. The author did not run the corrected tests; the parent will score after integration. Any subsequent twelve-of-twelve pass is exposed regression confirmation, not twelve blind holdout successes. No loader, architecture, or contract changes were made for this correction.

The integrated corrected set subsequently passed all twelve cases. The five valid
violation rows had zero false conformance at their first observation; all four
expected loading failures and both original clean controls behaved as labeled.
The mislabeled head-only note is excluded from that first-score interpretation.
There was no implementation change motivated by this set. Later runs are exposed
regression confirmation, and twelve small synthetic inputs do not establish a
population error rate.
