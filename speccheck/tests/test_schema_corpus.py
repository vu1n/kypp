"""Independently frozen SQL-002 cases; SQL is data, never executed.

Each record has id, sql (a tuple of migration-entry strings), columns,
and optionally idempotence. Labels describe bounded registry evidence only.
"""

import unittest


CASES = (
    {"id": "sc01", "sql": ("ALTER TABLE notes ADD COLUMN category TEXT",), "columns": "conforms"},
    {"id": "sc02", "sql": ("alter table notes add column attempts INTEGER NOT NULL DEFAULT -2;",), "columns": "conforms"},
    {"id": "sc03", "sql": ("ALTER TABLE notes ADD COLUMN caption TEXT DEFAULT 'can''t; DROP TABLE notes'",), "columns": "conforms"},
    {"id": "sc04", "sql": ('ALTER TABLE "notes" ADD COLUMN "NOT NULL" TEXT',), "columns": "conforms"},
    {"id": "sc05", "sql": ("ALTER /* boundary */ TABLE notes ADD COLUMN active INTEGER NOT /* gap */ NULL DEFAULT 1",), "columns": "conforms"},
    {"id": "sc06", "sql": ("ALTER TABLE notes ADD COLUMN tag TEXT; ALTER TABLE notes ADD COLUMN weight REAL DEFAULT 0.25;",), "columns": "conforms"},
    {"id": "sc07", "sql": ("ALTER TABLE notes ADD COLUMN category TEXT NOT NULL",), "columns": "violates"},
    {"id": "sc08", "sql": ("ALTER TABLE notes ADD COLUMN category TEXT NOT NULL /* DEFAULT 'unknown' */",), "columns": "violates"},
    {"id": "sc09", "sql": ('ALTER TABLE notes ADD COLUMN "DEFAULT" TEXT NOT NULL',), "columns": "violates"},
    {"id": "sc10", "sql": ("ALTER TABLE notes ADD COLUMN category TEXT NOT NULL -- DEFAULT 'unknown'\n;",), "columns": "violates"},
    {"id": "sc11", "sql": ("DROP TABLE notes; -- ALTER TABLE notes ADD COLUMN tag TEXT",), "columns": "violates"},
    {"id": "sc12", "sql": ("ALTER TABLE notes RENAME COLUMN caption TO title; /* ADD COLUMN */",), "columns": "violates"},
    {"id": "sc13", "sql": ("ALTER TABLE notes ADD COLUMN tag TEXT; DROP INDEX notes_tag;",), "columns": "violates"},
    {"id": "sc14", "sql": ("ALTER TABLE notes ADD COLUMN score INTEGER DEFAULT (2 + 3)",), "columns": "unresolved"},
    {"id": "sc15", "sql": ("ALTER TABLE notes ADD COLUMN score INTEGER CHECK (score >= 0)",), "columns": "unresolved"},
    {"id": "sc16", "sql": ("ALTER TABLE notes ADD COLUMN doubled INTEGER GENERATED ALWAYS AS (score * 2) VIRTUAL",), "columns": "unresolved"},
    {"id": "sc17", "sql": ("ALTER TABLE notes ADD COLUMN tag TEXT DEFAULT 'unfinished",), "columns": "unresolved"},
    {"id": "sc18", "sql": ("/* ALTER TABLE notes ADD COLUMN tag TEXT */",), "columns": "unresolved"},
    {"id": "sc19", "sql": ("ALTER TABLE notes ADD COLUMN score INTEGER DEFAULT (2 + 3)", "ALTER TABLE notes ADD COLUMN category TEXT NOT NULL"), "columns": "violates"},
    {"id": "sc20", "sql": ('CREATE TABLE IF NOT EXISTS "schema_version" (version INTEGER);',), "columns": "violates", "idempotence": "violates"},
    {"id": "sc21", "sql": ("ALTER TABLE notes ADD COLUMN tag TEXT DEFAULT 'CREATE TABLE schema_version (version INTEGER)'",), "columns": "conforms", "idempotence": "unresolved"},
    {"id": "sc22", "sql": ("ALTER TABLE notes ADD COLUMN tag TEXT; /* CREATE TABLE schema_version (version INTEGER) */",), "columns": "conforms", "idempotence": "unresolved"},
)


def _outcome(label: str) -> str:
    if label == "conforms":
        return "no_counterexample"
    return label


def case_tree(case):
    """Build fixture source without evaluating or executing SQL/Python input."""
    from speccheck.fixtures import clean_tree

    tree = clean_tree()
    tree["kypp/store.py"] = "_MIGRATIONS = " + repr(list(case["sql"])) + "\n"
    return tree


class SchemaCorpusTest(unittest.TestCase):
    def test_frozen_corpus(self):
        from speccheck.engine import run_check
        from speccheck.fixtures import clean_tree

        for case in CASES:
            with self.subTest(case=case["id"]):
                report = run_check(clean_tree(), case_tree(case), baseline_sha="schema-corpus-baseline", head_sha=case["id"])
                statuses = {v.rule_id: v.status for v in report.verdicts}
                # The frozen label "conforms" is a clean scan. The checker records that as
                # no_counterexample and does not emit a pass.
                self.assertEqual(statuses["schema-add-column"], _outcome(case["columns"]))
                if "idempotence" in case:
                    self.assertEqual(statuses["schema-idempotence"], _outcome(case["idempotence"]))


if __name__ == "__main__":
    unittest.main()
