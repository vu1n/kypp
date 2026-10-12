"""Benchmark splits stay separate. Smoke is not an accuracy metric."""

import unittest

from speccheck.bench import run_benchmark
from speccheck.bench_cases import all_cases
from speccheck.model import Verdict


class BenchTest(unittest.TestCase):
    def test_splits_and_rates(self):
        cases = all_cases()
        splits = {c.split for c in cases}
        self.assertEqual(splits, {"tuning", "holdout", "smoke"})
        ids = [c.id for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(1 for c in cases if c.split == "smoke"), 30)
        scores = {s.split: s for s in run_benchmark()}
        for name in ("tuning", "holdout", "smoke"):
            self.assertTrue(scores[name].ok, scores[name].failures)
        self.assertEqual(scores["tuning"].false_conformance_rate, 0.0)
        self.assertEqual(scores["holdout"].false_conformance_rate, 0.0)
        self.assertGreater(scores["tuning"].abstention_rate, 0.0)
        self.assertGreater(scores["tuning"].coverage, 0.0)
        self.assertGreater(scores["holdout"].abstention_rate, 0.0)
        self.assertGreater(scores["holdout"].coverage, 0.0)
        self.assertTrue(scores["smoke"].smoke_only)
        self.assertEqual(scores["smoke"].false_flag_rate, 0.0)
        self.assertIsNone(scores["smoke"].false_conformance_rate)

    def test_conforms_requires_scope_and_coverage(self):
        with self.assertRaises(ValueError):
            Verdict(
                rule_id="x",
                status="conforms",
                claim_text="c",
                scope="",
                coverage="looked",
                detail="d",
            )


if __name__ == "__main__":
    unittest.main()
