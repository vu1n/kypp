"""Benchmark splits stay separate. Smoke is not an accuracy metric."""

import unittest
from unittest.mock import patch

from speccheck.bench import run_benchmark, score_split
from speccheck.bench_cases import Case, all_cases, regression_cases
from speccheck.holdout_cases import holdout_cases
from speccheck.model import Report, Verdict


class BenchTest(unittest.TestCase):
    def test_splits_and_rates(self):
        cases = all_cases()
        splits = {c.split for c in cases}
        self.assertEqual(splits, {"tuning", "regression", "gap-holdout", "smoke"})
        ids = [c.id for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(1 for c in cases if c.split == "smoke"), 30)
        scores = {s.split: s for s in run_benchmark(["tuning", "regression", "smoke"])}
        for name in ("tuning", "regression", "smoke"):
            self.assertTrue(scores[name].ok, scores[name].failures)
        self.assertEqual(scores["tuning"].known_violation_detection, 1.0)
        self.assertEqual(scores["regression"].known_violation_detection, 1.0)
        self.assertEqual(scores["tuning"].false_positive_rate, 0.0)
        self.assertEqual(scores["regression"].false_positive_rate, 0.0)
        self.assertGreater(scores["tuning"].unresolved_rate, 0.0)
        self.assertIsNotNone(scores["regression"].mutation_detection)
        self.assertEqual(scores["regression"].mutation_detection, 1.0)
        self.assertTrue(scores["smoke"].smoke_only)
        self.assertEqual(scores["smoke"].false_positive_rate, 0.0)
        self.assertIsNone(scores["smoke"].known_violation_detection)

    def test_frozen_worker_holdout_stays_nine_cases(self):
        frozen = holdout_cases()
        frozen_ids = [case.id for case in frozen]
        self.assertEqual(frozen_ids, [f"worker_fresh_{i:02d}" for i in range(1, 10)])
        self.assertTrue(all(case.split == "regression" for case in frozen))
        extras = [case.id for case in all_cases() if case.split == "holdout"]
        self.assertEqual(extras, [])

    def test_legacy_holdout_preserved_as_regression(self):
        cases = regression_cases()
        self.assertEqual(len(cases), 13)
        self.assertTrue(all(c.split == "regression" for c in cases))
        self.assertTrue(all(c.id.startswith("holdout_") for c in cases))

    def test_gap_holdout_is_a_split_choice(self):
        from speccheck.cli import main

        self.assertEqual(main(["bench", "--split", "gap-holdout"]), 0)

    def test_default_split_selection_without_evaluating_holdout(self):
        with patch("speccheck.bench.score_split") as score:
            run_benchmark()
        self.assertEqual(
            [call.args[0] for call in score.call_args_list],
            ["tuning", "regression", "gap-holdout", "smoke"],
        )
        for call in score.call_args_list:
            self.assertTrue(call.args[1])
            self.assertTrue(all(c.split == call.args[0] for c in call.args[1]))

    def test_missing_verdict_counts_as_unresolved_and_a_miss(self):
        case = Case("missing", "regression", "missing rows", {}, {},
                    expect_status={"bad": "violates", "clean": "no_counterexample"})
        report = Report("base", "head", verdicts=[Verdict(
            rule_id="bad", status="no_counterexample", claim_text="c", scope="s",
            coverage="looked", detail="d",
        )])
        with patch("speccheck.bench._run", return_value=report):
            score = score_split("regression", [case])
        self.assertEqual(score.judged, 2)
        self.assertEqual(score.known_violation_detection, 0.0)
        self.assertEqual(score.unresolved_rate, 0.5)
        self.assertEqual(score.false_positive_rate, 0.0)
        self.assertFalse(score.ok)

    def test_false_positive_is_a_violation_on_a_clean_row(self):
        cases = [Case(str(i), "regression", "missing", {}, {},
                      expect_status={"rule": "no_counterexample"}) for i in range(2)]
        false_hit = Report("base", "head", verdicts=[Verdict(
            rule_id="rule", status="violates", severity="high", claim_text="c", scope="s",
            coverage="looked", detail="d",
        )])
        with patch("speccheck.bench._run", side_effect=[false_hit, Report("base", "head")]):
            score = score_split("regression", cases)
        self.assertEqual(score.judged, 2)
        self.assertEqual(score.false_positive_rate, 0.5)
        self.assertEqual(score.unresolved_rate, 0.5)
        self.assertFalse(score.ok)

    def test_empty_split_fails(self):
        score = score_split("gap-holdout", [])
        self.assertFalse(score.ok)
        self.assertIn("empty split", score.failures[0])

    def test_bounded_requires_scope_and_coverage(self):
        with self.assertRaises(ValueError):
            Verdict(
                rule_id="x",
                status="bounded",
                claim_text="c",
                scope="",
                coverage="looked",
                detail="d",
            )


if __name__ == "__main__":
    unittest.main()
