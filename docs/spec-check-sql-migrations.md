# SQL migration checker increment

The checker distinguishes real SQL clauses and operations from words in comments,
string literals and quoted names. A NOT NULL column cannot borrow a DEFAULT from
a comment, and a DROP cannot borrow ADD COLUMN. Creating a recognized schema-version
table violates both the additive-shape and version-table registry checks.

## Before and after

All four original evaluator expectations failed on main before implementation.
Tests now cover these paths without executing the supplied SQL.

| Input shape | Before | After |
| --- | --- | --- |
| NOT NULL with DEFAULT only inside a comment | conforms | violates |
| DROP TABLE with ADD COLUMN only inside a comment | conforms | violates |
| CREATE TABLE schema_version with a simple column list | additive rule conforms | both relevant rules violate |
| ADD COLUMN DEFAULT string containing NOT NULL/schema_version | false version-table violation | conforms to syntax rule; runtime idempotence unresolved |

The registry still reads its exact pinned `additive-migrations@0002` bullet from
the baseline. That rule is retired, not an active governance constraint. This
increment does not rewrite it or supply runtime proof of idempotence, default
semantics, existing-row compatibility or database acceptance.

## Covered evidence

The stdlib lexer separates comments, strings, quoted identifiers and statement
boundaries. The analyzer recognizes ordinary typed ADD COLUMN declarations with
literal defaults, selected DROP/RENAME operations, and simple CREATE TABLE shapes.
Unsupported or ambiguous syntax abstains. Known counterexamples remain visible
alongside unrelated SQL or nonliteral initializer gaps. Partial Python migration
facts retain original entry positions and use a new cache namespace, so older
all-or-nothing facts cannot hide retained literals.
The inventory covers a unique list initializer, not later runtime mutations.

Lexing has character, token, parenthesis-depth and statement limits. Crossing a
limit yields an unresolved coverage gap. This deliberately bounded grammar is
informed by SQLite's [ALTER TABLE](https://www.sqlite.org/lang_altertable.html),
[comment](https://www.sqlite.org/lang_comment.html), and
[DROP INDEX](https://www.sqlite.org/lang_dropindex.html) documentation; it is not
a replacement for the database engine's parser.

## Independent evaluation

The corpus author froze 22 cases before inspecting or scoring the implementation.
The first evaluation at `6716f88f6561e83102dcdc4f8ab42d808c6fd924` had zero false
conformance, 27.3% column abstention and 72.7% coverage. One expected violation
for DROP INDEX abstained; that outcome motivated a parser correction. Labels were
unchanged and all cases are now exposed regression confirmation. See
[freeze provenance and first results](spec-check-schema-evaluation.md) for exact
denominators, the original commit/hash and limits on accuracy claims.

## Validation and handoff

- Combined checker suite: 48 tests passed after the DROP INDEX and budget fixes.
- Existing tuning/regression/previously exposed holdout/smoke benchmark passed.
- Live advisory report against published main: 8 conforms, 0 violates,
  7 unsupported, 3 unresolved. Exit 0 means a report was produced.
- Independent structural review found no high-confidence blocker. Fresh immutable
  pushed-head correctness review, separate static security review, mutants and
  exact-head CI evidence are recorded in the draft PR.

PR24 merged externally as `0e80abc1bcafa1aed1ff312bc2827126a9290011` during this
work. Its published parser, corpus and CI discovery step were integrated before
combined validation; this PR has no unmerged dependency and adds no duplicate CI
step. Kypp/Huddles substrate and Pillbox worker-usage work remain separate.
No runtime Kypp/Worker code, active decisions, deployment or PR24 branch was changed.

Complex CREATE forms, CHECK/generated/REFERENCES constraints, parenthesized
defaults and other unsupported syntax still abstain. `brief doctor` retains the
same three stale memory-scope pins in untouched arbiter/distill code on main.
The draft PR awaits the user's separate merge decision.
