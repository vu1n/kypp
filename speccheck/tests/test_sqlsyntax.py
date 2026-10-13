"""SQL evidence regressions, independent of engine acceptance."""
import unittest
import tempfile
from pathlib import Path

from speccheck.cache import Cache
from speccheck.sqlsyntax import analyze_migration
from speccheck.syntax import cached_python_migrations, classify_migration
from speccheck.engine import run_check
from speccheck.fixtures import clean_tree


class SQLSyntaxTest(unittest.TestCase):
    def test_policy_cases(self):
        cases = {
            "ALTER TABLE t ADD COLUMN flag TEXT NOT NULL /* DEFAULT 'x' */": 'not_null_without_default',
            'DROP TABLE memory_claims /* ADD COLUMN */': 'not_add_column',
            'CREATE TABLE schema_version (n INTEGER)': 'version_table',
            'CREATE TABLE IF NOT EXISTS schema_version (n INTEGER NOT NULL DEFAULT 0)': 'version_table',
            'DROP TABLE ' + 't.' * 1500 + 't': 'unknown',
            "ALTER TABLE t ADD COLUMN flag TEXT DEFAULT 'NOT NULL schema_version'": 'ok',
            "ALTER TABLE t ADD COLUMN flag TEXT DEFAULT 'a; b''c' NOT NULL": 'ok',
            'ALTER TABLE "schema_version" ADD COLUMN "NOT NULL" TEXT': 'ok',
            'ALTER TABLE [t] ADD COLUMN `DEFAULT` INTEGER DEFAULT -1': 'ok',
            'ALTER TABLE t ADD COLUMN c TEXT NOT /*comment*/ NULL': 'not_null_without_default',
            'ALTER TABLE t ADD c REAL DEFAULT 1.2': 'ok',
            'ALTER TABLE t DROP COLUMN c': 'not_add_column',
            'ALTER TABLE t RENAME COLUMN c TO d': 'not_add_column',
            'CREATE TABLE "main"."schema_version" (n INTEGER)': 'version_table',
            'ALTER TABLE t ADD COLUMN c TEXT DEFAULT': 'unknown',
            'ALTER TABLE t ADD COLUMN c TEXT CHECK (c IS NOT NULL)': 'unknown',
            'ALTER TABLE t ADD COLUMN c TEXT DEFAULT (1)': 'unknown',
            'ALTER TABLE t ADD COLUMN c TEXT DEFAULT "x"': 'unknown',
            'ALTER TABLE t ADD COLUMN c TEXT; SELECT 1': 'unknown',
            'DROP TABLE t; SELECT 1': 'not_add_column',
            'CREATE TABLE schema_version AS SELECT 1': 'unknown',
            'ALTER TABLE t ADD COLUMN c TEXT /*': 'unknown',
            'ALTER TABLE t ADD COLUMN c TEXT DEFAULT \'unterminated': 'unknown',
            '': 'unknown',
        }
        for sql, expected in cases.items():
            with self.subTest(sql=sql):
                self.assertEqual(classify_migration(sql), expected)
                self.assertTrue(analyze_migration(sql).reasons)

    def test_bounds(self):
        large = 'ALTER TABLE t ADD COLUMN c TEXT /*' + 'x' * 100001 + '*/'
        self.assertEqual(classify_migration(large), 'unknown')
        nested = analyze_migration('ALTER TABLE t ADD COLUMN c TEXT DEFAULT ' + '(' * 33 + '1' + ')' * 33)
        self.assertEqual(nested.kind, 'unknown')
        self.assertIn('deeply nested', ' '.join(nested.reasons))

    def test_statement_budget_includes_final_statement_without_semicolon(self):
        statement = 'ALTER TABLE t ADD COLUMN c TEXT'
        self.assertEqual(classify_migration(';'.join([statement] * 128)), 'ok')
        self.assertEqual(classify_migration(';'.join([statement] * 129)), 'unknown')

    def test_old_migration_cache_cannot_hide_partial_literals(self):
        source = '_MIGRATIONS = [dynamic(), "DROP TABLE t"]'
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory))
            cache.syntax(source, 'py-migrations', lambda: {'covered': False, 'error': 'nonliteral', 'entries': []})
            fresh = Cache(Path(directory))
            facts = cached_python_migrations(fresh, source)
            self.assertEqual(facts['entries'], [None, 'DROP TABLE t'])
            self.assertEqual(fresh.syntax_hits, 0)

    def verdicts(self, declaration):
        baseline = clean_tree()
        head = dict(baseline)
        head['kypp/store.py'] = '_MIGRATIONS = ' + declaration
        report = run_check(baseline, head, baseline_sha='base', head_sha='sql')
        return {v.rule_id: v for v in report.verdicts}

    def test_real_version_table_violates_both_rules(self):
        verdicts = self.verdicts(repr(['CREATE TABLE schema_version (n INTEGER)']))
        for rule in ('schema-add-column', 'schema-idempotence'):
            self.assertEqual(verdicts[rule].status, 'violates')

    def test_comment_and_string_decoys(self):
        for sql in ("ALTER TABLE t ADD COLUMN c TEXT NOT NULL /* DEFAULT 'x' */", 'DROP TABLE t /* ADD COLUMN */'):
            self.assertEqual(self.verdicts(repr([sql]))['schema-add-column'].status, 'violates')
        verdicts = self.verdicts(repr(["ALTER TABLE t ADD COLUMN c TEXT DEFAULT 'NOT NULL schema_version'"]))
        self.assertEqual(verdicts['schema-add-column'].status, 'conforms')
        self.assertEqual(verdicts['schema-idempotence'].status, 'unresolved')

    def test_counterexamples_survive_entry_gaps(self):
        for sql in ('DROP TABLE t', 'CREATE TABLE schema_version (n INTEGER)'):
            verdicts = self.verdicts('[dynamic(), ' + repr(sql) + ']')
            self.assertEqual(verdicts['schema-add-column'].status, 'violates')
            if sql.startswith('CREATE'):
                self.assertEqual(verdicts['schema-idempotence'].status, 'violates')
        self.assertEqual(self.verdicts('[dynamic(), "ALTER TABLE t ADD COLUMN c TEXT"]')['schema-add-column'].status, 'unresolved')
        self.assertEqual(self.verdicts(repr(['SELECT 1']))['schema-add-column'].status, 'unresolved')
