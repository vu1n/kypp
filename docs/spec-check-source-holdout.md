# Independent source inventory holdout

INV-004 freezes twelve fresh end-to-end cases in `speccheck/tests/test_source_inventory_holdout.py`. The author read AGENTS.md, PLAN.md, the public CLI, report model, fixtures, reference scanner, and the feature map. The author did not inspect the source loader, its implementation tests, the existing corpus, or other implementation/corpus worktrees. No holdout cases were executed before this freeze.

The sentinel uses the public reference grammar: project `kypp`, document `holdout-missing`, revision `0042`, anchor `retained`. The document intentionally does not exist. Its presence must produce a doc-reference advisory violation with exit zero and visible literal path and reference evidence. The token is constructed from separate strings in the test source to prevent repository scans from treating fixture data as a real reference.

The frozen denominators are six content-retention violations, four explicit source-loading errors, and two clean controls, for twelve cases total. Five cases use committed head mode; seven use worktree mode. The six retention paths cover every selected prefix and include spaces, a tab, a newline, Unicode, literal glob metacharacters, and a quote/backslash. Error cases exercise invalid UTF-8 and symlinks in both head modes. The clean cases cover intentional worktree deletion and invalid/out-of-scope files, including a prefix lookalike and a dangling symlink.

Errors must return nonzero with an explicit diagnostic and leave no Markdown report, JSON report, or cache entries. Symlink cases point outside the temporary repository. This holdout does not inject permission faults, measure performance, cover Git submodules, or claim exhaustive unreadable-file coverage. No accuracy score or implementation result is claimed here.

Run after freeze against the settled implementation:

```sh
python -m unittest discover -s speccheck/tests -p test_source_inventory_holdout.py -v
```

The freeze commit and SHA-256 of the input file are supplied in the author handoff. Record the first score separately without changing these frozen inputs or labels; any later repair must preserve this original freeze and disclose exposure.
