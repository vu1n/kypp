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
            "UPDATE t SET `authority`='verified'",
            "INSERT INTO [memory_claims]([authority]) VALUES ('verified')",
        ):
            delete_bad, _delete_gap, verified_bad, verified_gap = sql_write_flags(statement, False)
            self.assertTrue(verified_bad, statement)
            self.assertFalse(verified_gap, statement)
            self.assertFalse(delete_bad, statement)

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
            (False, False, False, False),
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
