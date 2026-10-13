"""Gaps closed after the advisory checker landed: rank mirror, MCP models, prose, pins, script."""

import unittest
from pathlib import Path

from speccheck.cli import main
from speccheck.engine import run_check
from speccheck.fixtures import clean_tree, ref
from speccheck.syntax import typescript_authority_rank

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
