"""Regressions for the four review findings on the advisory checker."""

import unittest
from pathlib import Path

from speccheck.engine import run_check
from speccheck.fixtures import VOCAB_PY, clean_tree

ROOT = Path(__file__).resolve().parents[2]

MARKERS = {
    "eval_mcp_no_verify": "doc://kypp/verify-operator-only@0002#operator-only-verify",
    "eval_verify_timeout": "doc://kypp/verify-operator-only@0002#operator-only-verify",
    "eval_mcp_authority": "doc://kypp/authority-order@0004#authority-dominates",
    "eval_writers": "doc://kypp/authority-order@0004#authority-dominates",
    "eval_rank": "doc://kypp/authority-order@0004#authority-dominates",
}


def _status(tree: dict[str, str], rule_id: str) -> str | None:
    report = run_check(tree, tree, baseline_sha="base", head_sha="head", baseline_paths=sorted(tree), head_paths=sorted(tree))
    for verdict in report.verdicts:
        if verdict.rule_id == rule_id:
            return verdict.status
    return None


class ContextMarkerTest(unittest.TestCase):
    def test_evaluators_carry_pinned_context_markers(self):
        lines = (ROOT / "speccheck/evaluate.py").read_text().splitlines()
        for index, line in enumerate(lines):
            for name, ref in list(MARKERS.items()):
                if line.startswith(f"def {name}("):
                    previous = next(lines[i].strip() for i in range(index - 1, -1, -1) if lines[i].strip())
                    self.assertTrue(previous.startswith("# Context:"), previous)
                    self.assertIn(ref, previous)
                    self.assertIn(" — ", previous)
                    del MARKERS[name]
        self.assertEqual(MARKERS, {})


class RankOrderTest(unittest.TestCase):
    def test_reversal_and_unproved_rank_are_not_conformance(self):
        reversed_tree = clean_tree()
        reversed_tree["kypp/arbiter.py"] = reversed_tree["kypp/arbiter.py"].replace(
            'return (AUTHORITY_RANK.get(c.authority, 0), c.status == "accepted")',
            'return (list(reversed(AUTHORITIES)).index(c.authority), c.status == "accepted")',
            1,
        )
        self.assertEqual(_status(reversed_tree, "authority-rank-order"), "violates")

        opaque = clean_tree()
        opaque["kypp/arbiter.py"] = opaque["kypp/arbiter.py"].replace(
            'return (AUTHORITY_RANK.get(c.authority, 0), c.status == "accepted")',
            'return (blend(AUTHORITY_RANK, c.authority), c.status == "accepted")',
            1,
        )
        self.assertEqual(_status(opaque, "authority-rank-order"), "unresolved")

        live = _status(
            {
                **clean_tree(),
                "kypp/vocab.py": (ROOT / "kypp/vocab.py").read_text(),
                "kypp/arbiter.py": (ROOT / "kypp/arbiter.py").read_text(),
            },
            "authority-rank-order",
        )
        self.assertEqual(live, "conforms")
        self.assertIn('Literal["agent", "verified", "human"]', VOCAB_PY)


class InputSchemaTest(unittest.TestCase):
    def test_resolved_inline_schema_with_verify_violates(self):
        tree = clean_tree()
        tree["worker/src/mcp.ts"] = (
            'const claimSchema = { type: "object", properties: { subject: { type: "string" }, verify: { type: "string" } } };\n'
            + tree["worker/src/mcp.ts"].replace(
                """    inputSchema: {
      type: "object",
      properties: {
        subject: { type: "string" },
        content: { type: "string" },
      },
    },""",
                "    inputSchema: claimSchema,",
                1,
            )
        )
        self.assertEqual(_status(tree, "mcp-no-verify-argument"), "violates")


if __name__ == "__main__":
    unittest.main()
