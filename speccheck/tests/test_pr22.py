"""Regression: PR #22 added a verify argument to the MCP claim tool and a false conforms line."""

import unittest

from speccheck.engine import run_check
from speccheck.fixtures import clean_tree, with_pr22
from speccheck.report import render_markdown


class PR22Test(unittest.TestCase):
    def test_pr22_mcp_claim_verify_arg_violates(self):
        baseline = clean_tree()
        head = with_pr22(baseline)
        report = run_check(baseline, head, baseline_sha="base", head_sha="pr22")
        verdict = next(v for v in report.verdicts if v.rule_id == "mcp-no-verify-argument")
        self.assertEqual(verdict.status, "violates")
        self.assertEqual(verdict.severity, "high")
        self.assertIn("claim", verdict.counterexample)
        self.assertIn("verify", verdict.counterexample)
        self.assertNotIn("P1", verdict.severity)
        text = render_markdown(report)
        self.assertIn("does not block merge", text)
        self.assertNotIn("P1", text)
        self.assertIn("operator-only-verify conforms", head[".brief/SIGNOFF"])
        self.assertEqual(verdict.status, "violates")


if __name__ == "__main__":
    unittest.main()
