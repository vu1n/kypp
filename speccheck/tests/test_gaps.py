"""Gaps closed after the advisory checker landed: rank mirror, MCP models, prose, pins, script."""

import unittest
from pathlib import Path

from speccheck.cli import main
from speccheck.engine import run_check
from speccheck.fixtures import MEMORY_TS, clean_tree, ref, with_structural_specs
from speccheck.syntax import python_mcp_tools, sql_write_flags, typescript_authority_rank

ROOT = Path(__file__).resolve().parents[2]


def _report(tree: dict[str, str]):
    return run_check(tree, tree, baseline_sha="base", head_sha="head", baseline_paths=sorted(tree), head_paths=sorted(tree))


class ConsoleScriptTest(unittest.TestCase):
    def test_pyproject_registers_speccheck(self):
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('speccheck = "speccheck.cli:main"', text)
        self.assertIn('packages = ["kypp", "speccheck"]', text)
        self.assertTrue(callable(main))


class TypescriptRankTest(unittest.TestCase):
    def test_live_memory_order_matches_python(self):
        facts = typescript_authority_rank((ROOT / "worker/src/memory.ts").read_text())
        self.assertEqual(facts["order"], ["agent", "verified", "human"])

    def test_incidental_rank_call_is_not_the_comparator(self):
        source = MEMORY_TS.replace(
            "const ra = rank(a), rb = rank(b);",
            "rank(a);\n  const ra = [a.confidence], rb = [b.confidence];",
        )
        facts = typescript_authority_rank(source)
        self.assertEqual(facts["order"], "unknown")


class ExtraPolicyTest(unittest.TestCase):
    def _tool(self, body: str) -> dict:
        source = body + (
            "\ndef build_mcp(store):\n    @mcp.tool()\n    def claim(args: ClaimArgs) -> str:\n        return 'ok'\n"
        )
        facts = python_mcp_tools(source)
        self.assertEqual(len(facts["tools"]), 1)
        return facts["tools"][0]

    def test_only_literal_forbid_or_ignore_is_closed(self):
        enum_open = self._tool(
            "class ClaimArgs(BaseModel):\n    class Config:\n        extra = Extra.allow\n    subject: str\n"
        )
        self.assertTrue(enum_open["gaps"])
        self.assertNotIn("subject", enum_open["params"])
        for policy in ('"forbid"', '"ignore"'):
            with self.subTest(policy=policy):
                closed = self._tool(
                    "class ClaimArgs(BaseModel):\n    class Config:\n        extra = "
                    + policy
                    + "\n    subject: str\n"
                )
                self.assertEqual(closed["gaps"], [])
                self.assertIn("subject", closed["params"])
        nonliteral = self._tool(
            "class ClaimArgs(BaseModel):\n    model_config = ConfigDict(extra=Extra.allow)\n    subject: str\n"
        )
        self.assertTrue(nonliteral["gaps"])
        built = self._tool("class ClaimArgs(BaseModel):\n    model_config = OTHER\n    subject: str\n")
        self.assertTrue(built["gaps"])


class SqlIdentifierTest(unittest.TestCase):
    def test_quoted_protected_tables_and_authority_columns(self):
        for statement in (
            "DELETE FROM [memory_claims]",
            'DELETE FROM "memory_claims"',
            "DELETE FROM `observations`",
            "DELETE FROM main.[memory_claims]",
            "DELETE FROM [main].[observations]",
        ):
            delete_bad, delete_gap, verified_bad, verified_gap = sql_write_flags(statement, False)
            self.assertTrue(delete_bad, statement)
            self.assertFalse(delete_gap, statement)
            self.assertFalse(verified_bad or verified_gap, statement)
        for statement in (
            "UPDATE memory_claims SET [authority] = 'verified'",
            'UPDATE memory_claims SET "authority"="verified"',
            "INSERT INTO [memory_claims]([authority]) VALUES ('verified')",
        ):
            delete_bad, _delete_gap, verified_bad, verified_gap = sql_write_flags(statement, False)
            self.assertTrue(verified_bad, statement)
            self.assertFalse(verified_gap, statement)
            self.assertFalse(delete_bad, statement)
        for statement in (
            "UPDATE t SET `authority`='verified'",
            "UPDATE build_jobs SET authority='verified'",
            "UPDATE observations SET authority='verified'",
            "INSERT INTO build_jobs(authority) VALUES ('verified')",
            "ALTER TABLE build_jobs ADD COLUMN authority TEXT DEFAULT 'verified'",
        ):
            self.assertEqual(sql_write_flags(statement, False), (False, False, False, False), statement)

    def test_verified_sql_violation_requires_memory_claims(self):
        self.assertTrue(sql_write_flags("UPDATE main.memory_claims SET authority='verified'", False)[2])
        self.assertTrue(sql_write_flags(
            "ALTER TABLE memory_claims ADD COLUMN authority TEXT DEFAULT 'verified'", False,
        )[2])
        tree = clean_tree()
        tree["kypp/extra.py"] = (
            "def promote(db):\n"
            "    db.execute(\"UPDATE build_jobs SET authority='verified'\")\n"
        )
        verdict = next(item for item in _report(tree).verdicts if item.rule_id == "verified-authority-writer")
        self.assertEqual(verdict.status, "no_counterexample")
        self.assertIsNone(verdict.counterexample)
        tree["kypp/extra.py"] = (
            "def promote(db):\n"
            "    db.execute(\"UPDATE memory_claims SET authority='verified'\")\n"
        )
        verdict = next(item for item in _report(tree).verdicts if item.rule_id == "verified-authority-writer")
        self.assertEqual(verdict.status, "violates")
        self.assertEqual(verdict.severity, "high")
        self.assertIn("kypp/extra.py", verdict.counterexample or "")

    def test_lookalike_memory_store_is_a_counterexample(self):
        writer = (
            "class MemoryStore:\n"
            "    def mark_verified(self, db):\n"
            "        db.execute(\"UPDATE memory_claims SET authority='verified' WHERE id=1\")\n"
        )
        allowed = clean_tree()
        allowed["kypp/store.py"] += "\n" + writer
        allowed_verdict = next(
            item for item in _report(allowed).verdicts if item.rule_id == "verified-authority-writer"
        )
        self.assertEqual(allowed_verdict.status, "no_counterexample")
        self.assertIsNone(allowed_verdict.counterexample)
        lookalike = clean_tree()
        lookalike["kypp/store.py"] += "\n" + writer
        lookalike["kypp/backdoor.py"] = writer
        verdict = next(item for item in _report(lookalike).verdicts if item.rule_id == "verified-authority-writer")
        self.assertEqual(verdict.status, "violates")
        self.assertEqual(verdict.severity, "high")
        self.assertIn("kypp/backdoor.py", verdict.counterexample or "")
        self.assertNotIn("kypp/store.py", verdict.counterexample or "")
        claim = clean_tree()
        claim["kypp/backdoor.py"] = (
            "class MemoryStore:\n"
            "    def mark_verified(self, store):\n"
            "        store.claim('fact', 's', 'c', authority='verified')\n"
        )
        claim_verdict = next(item for item in _report(claim).verdicts if item.rule_id == "verified-authority-writer")
        self.assertEqual(claim_verdict.status, "violates")
        self.assertIn("kypp/backdoor.py", claim_verdict.counterexample or "")

    def test_insert_maps_values_and_abstains_when_unmapped(self):
        self.assertEqual(
            sql_write_flags(
                "INSERT INTO memory_claims(subject, authority) VALUES ('s', 'verified'), ('t', 'agent')",
                False,
            )[2],
            True,
        )
        self.assertEqual(
            sql_write_flags("INSERT INTO memory_claims(subject, authority) SELECT 's', 'verified'", False)[2],
            True,
        )
        self.assertEqual(
            sql_write_flags("INSERT INTO memory_claims(authority) SELECT 'agent'", False),
            (False, False, False, False),
        )
        self.assertEqual(
            sql_write_flags("INSERT INTO memory_claims(subject, authority) VALUES ('s', ?)", False),
            (False, False, False, True),
        )
        _delete_bad, _delete_gap, verified_bad, verified_gap = sql_write_flags(
            "INSERT INTO memory_claims(subject, authority) SELECT * FROM other",
            False,
        )
        self.assertFalse(verified_bad)
        self.assertTrue(verified_gap)
        _delete_bad, _delete_gap, verified_bad, verified_gap = sql_write_flags(
            "INSERT INTO memory_claims(authority) VALUES ('s', 'verified')",
            False,
        )
        self.assertFalse(verified_bad)
        self.assertTrue(verified_gap)

    def test_unmapped_insert_is_unresolved(self):
        tree = clean_tree()
        tree["kypp/extra.py"] = (
            "def promote(db):\n"
            "    db.execute(\"INSERT INTO memory_claims(subject, authority) SELECT * FROM other\")\n"
        )
        report = run_check(tree, tree, baseline_sha="base", head_sha="head", baseline_paths=sorted(tree), head_paths=sorted(tree))
        verdict = next(item for item in report.verdicts if item.rule_id == "verified-authority-writer")
        self.assertEqual(verdict.status, "unresolved")

    def test_upsert_verified_is_a_writer_and_other_shapes_abstain(self):
        self.assertTrue(sql_write_flags(
            "INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') "
            "ON CONFLICT(subject) DO UPDATE SET authority='verified'",
            False,
        )[2])
        self.assertEqual(
            sql_write_flags(
                "INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') "
                "ON CONFLICT(subject) DO UPDATE SET authority='agent'",
                False,
            )[2:],
            (False, True),
        )
        self.assertEqual(
            sql_write_flags(
                "INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') RETURNING id",
                False,
            )[2:],
            (False, True),
        )
        self.assertEqual(
            sql_write_flags("UPDATE memory_claims SET status='accepted' WHERE authority='verified'", False),
            (False, False, False, False),
        )
        sibling = sql_write_flags(
            "SELECT * FROM memory_claims WHERE x = ?; DELETE FROM memory_claims",
            False,
        )
        self.assertTrue(sibling[0])
        tree = clean_tree()
        tree["kypp/extra.py"] = (
            "def promote(db):\n"
            "    db.execute(\"INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') "
            "ON CONFLICT(subject) DO UPDATE SET authority='verified'\")\n"
        )
        verdict = next(item for item in _report(tree).verdicts if item.rule_id == "verified-authority-writer")
        self.assertEqual(verdict.status, "violates")
        self.assertEqual(verdict.severity, "high")
        self.assertIn("verified", verdict.counterexample or "")
        self.assertIn("mark_verified", verdict.detail)
        self.assertNotIn("conforms", verdict.status)

    def test_ordinary_sql_naming_a_protected_table_is_not_a_delete_finding(self):
        for table in ("memory_claims", "observations"):
            for statement in (
                f"SELECT id FROM {table}",
                f"CREATE TABLE {table}(id TEXT)",
                f"ALTER TABLE {table} ADD COLUMN note TEXT",
                f"INSERT INTO {table}(id) VALUES ('a')",
            ):
                delete_bad, delete_gap, _verified_bad, _verified_gap = sql_write_flags(statement, False)
                self.assertFalse(delete_bad, statement)
                self.assertFalse(delete_gap, statement)
        tree = with_structural_specs(clean_tree())
        tree["kypp/store.py"] = (
            "_MIGRATIONS = [\n"
            "    \"CREATE TABLE memory_claims(id TEXT)\",\n"
            "    \"CREATE TABLE observations(id TEXT)\",\n"
            "    \"ALTER TABLE memory_claims ADD COLUMN note TEXT\",\n"
            "    \"ALTER TABLE observations ADD COLUMN note TEXT\",\n"
            "    \"INSERT INTO memory_claims(id) VALUES ('a')\",\n"
            "    \"INSERT INTO observations(id) VALUES ('b')\",\n"
            "    \"SELECT id FROM memory_claims\",\n"
            "    \"SELECT id FROM observations\",\n"
            "]\n"
        )
        verdict = next(item for item in _report(tree).verdicts if item.rule_id == "no-delete-claims")
        self.assertEqual(verdict.status, "no_counterexample")
        self.assertNotIn("dynamic DELETE", verdict.coverage)
        self.assertNotIn("dynamic DELETE", verdict.detail)
        self.assertNotIn("conforms", verdict.status)

    def test_replace_function_in_a_read_only_query_is_not_delete_like(self):
        for table in ("memory_claims", "observations"):
            statement = f"SELECT replace(content, 'a', 'b') FROM {table}"
            delete_bad, delete_gap, verified_bad, verified_gap = sql_write_flags(statement, False)
            self.assertFalse(delete_bad, statement)
            self.assertFalse(delete_gap, statement)
            self.assertFalse(verified_bad, statement)
            self.assertFalse(verified_gap, statement)
        self.assertTrue(sql_write_flags("REPLACE INTO memory_claims(id) VALUES ('a')", False)[1])
        self.assertTrue(sql_write_flags("INSERT OR REPLACE INTO observations(id) VALUES ('a')", False)[1])

    def test_delete_like_constructions_are_candidates_and_parse_failures_stay_visible(self):
        for statement in (
            "DROP TABLE memory_claims",
            "DROP TABLE observations",
            "TRUNCATE memory_claims",
            "TRUNCATE TABLE observations",
            "REPLACE INTO memory_claims(id) VALUES ('a')",
            "INSERT OR REPLACE INTO observations(id) VALUES ('a')",
            "DELETE FROM \x00",
        ):
            delete_bad, delete_gap, _verified_bad, _verified_gap = sql_write_flags(statement, False)
            self.assertFalse(delete_bad, statement)
            self.assertTrue(delete_gap, statement)
        self.assertFalse(sql_write_flags("DROP TABLE projects", False)[1])
        self.assertFalse(sql_write_flags(
            "INSERT OR IGNORE INTO projects(name) SELECT project FROM memory_claims",
            False,
        )[1])
        tree = with_structural_specs(clean_tree())
        tree["kypp/broken.py"] = "def (\n"
        verdict = next(item for item in _report(tree).verdicts if item.rule_id == "no-delete-claims")
        self.assertEqual(verdict.status, "unresolved")
        self.assertIn("did not parse", verdict.coverage)
        self.assertNotIn("dynamic DELETE", verdict.coverage)
        self.assertNotEqual(verdict.status, "candidate")

    def test_bracket_delete_violates(self):
        tree = with_structural_specs(clean_tree())
        tree["kypp/wipe.py"] = 'def wipe(db):\n    db.execute("DELETE FROM [observations]")\n'
        report = run_check(tree, tree, baseline_sha="base", head_sha="head", baseline_paths=sorted(tree), head_paths=sorted(tree))
        verdict = next(item for item in report.verdicts if item.rule_id == "no-delete-claims")
        self.assertEqual(verdict.status, "violates")
        self.assertEqual(verdict.severity, "high")


class StalePinTest(unittest.TestCase):
    def test_two_pins_stay_two_advisories(self):
        tree = clean_tree()
        tree[".brief/docs/verify-operator-only/versions/v0001.md"] = tree[".brief/docs/verify-operator-only/versions/v0002.md"]
        tree[".brief/docs/authority-order/versions/v0001.md"] = tree[".brief/docs/authority-order/versions/v0004.md"]
        tree["kypp/note.py"] = (
            "# " + ref("kypp/verify-operator-only@0001#operator-only-verify") + "\n"
            "# " + ref("kypp/authority-order@0001#authority-dominates") + "\n"
        )
        stale = [a for a in _report(tree).advisories if a.code == "stale-pin"]
        self.assertEqual(len(stale), 2)
        detail = "\n".join(a.detail for a in stale)
        self.assertIn("verify-operator-only@0001", detail)
        self.assertIn("authority-order@0001", detail)
        self.assertIn("kypp/note.py:", detail)


if __name__ == "__main__":
    unittest.main()
