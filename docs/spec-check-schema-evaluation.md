# Independent schema corpus

SQL-002 froze 22 independently labeled migration cases in commit
`22f68268b268d3d52133ed88c98bb9a1f44831c1` on branch `schema-corpus`.
The SHA-256 of `speccheck/tests/test_schema_corpus.py` at that freeze is
`deb04165ba7b0a048f72747be55a4b13bbbe9b418fd847d1b7bb6e19cf496aaf`.
This documentation was added afterward so the original freeze identity remains
auditable. The parent integration may publish the original freeze reference.

The author read AGENTS.md, REVIEW-GUIDELINES.md, PLAN SQL-002, the retired
`.brief/docs/additive-migrations/versions/v0002.md` registry source, and existing
fixture/model/engine interfaces. The retired bullet is the existing checker's
source, not an active governance claim. Cases were authored from that policy and
SQLite's primary [ALTER TABLE documentation](https://www.sqlite.org/lang_altertable.html),
[column definition grammar](https://www.sqlite.org/syntax/column-def.html), and
[column constraint grammar](https://www.sqlite.org/syntax/column-constraint.html),
accessed 2026-10-13. Neither syntax.py nor evaluate.py source, SQL-001 source, nor
its workspace was inspected. Loading existing interfaces/constants is permitted;
no checker was run to derive or adjust labels.

`CASES` is a tuple of dictionary records with these exact fields:

| Field | Format | Meaning |
| --- | --- | --- |
| `id` | Unique string | Stable case identity |
| `sql` | Nonempty tuple of strings | Separate `_MIGRATIONS` entries; each can contain multiple statements |
| `columns` | `conforms`, `violates`, or `unresolved` | Expected `schema-add-column` status |
| `idempotence` | Optional status string | Expected `schema-idempotence` status when explicitly labeled |

`case_tree(case)` replaces fixture `kypp/store.py` with a Python literal migration
list. `SchemaCorpusTest.test_frozen_corpus` exercises `run_check`, verifies the
column verdict for every record, and checks idempotence only when specified.
Ordinary unittest discovery includes this test. There is no SQL execution, custom
benchmark split, CLI modification, or separate CI step.

The scope is bounded ordinary literal ADD COLUMN syntax. Labels distinguish
actual clauses from comment/string/quoted-identifier contents, include unsupported
and incomplete syntax that should abstain, retain known counterexamples alongside
unrelated evidence gaps, and include version-table creation and lexical controls.
Literal default recognition does not prove engine acceptance, default semantics,
compatibility with existing rows, or runtime idempotence. No case expects proof
of successful runtime replay. Unsupported constructs are not labeled conforming
merely because their text contains additive keywords.

At publication this corpus is **unscored**. Non-scoring validation comprised
Python compilation, static record-shape/uniqueness checks, `git diff --check`, and
Brief v0.4.2 features/check/pin. The parent evaluates only after SQL-001 settles,
using the existing metric conventions. Expectations must remain unchanged after
outcomes are observed. Once exposed and evaluated, these cases are regression
confirmation, not a reusable blind accuracy holdout. Small targeted sampling and
shared task requirements limit broader accuracy claims.
