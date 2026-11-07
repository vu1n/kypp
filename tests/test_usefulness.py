import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import usefulness  # noqa: E402


def shown(consumer, claim, surface="briefing", section="settled", rank=1):
    return {"consumer": consumer, "claim_id": claim, "surface": surface, "score": None, "subject": claim,
            "metadata": json.dumps({"position": rank, "section": section, "rank": rank})}


def rated(consumer, claim, score):
    return {"consumer": consumer, "claim_id": claim, "surface": "feedback", "score": score,
            "metadata": json.dumps({"verdict": usefulness.VERDICTS[score]})}


class Lift(unittest.TestCase):
    def test_a_claim_is_scored_against_its_slot(self):
        rows = []
        # "top" sits first in every briefing and is used 2 of 4 times, as the top slot is on average.
        # "deep" sits at rank 8 of recalls, where nothing else gets used, and is used 2 of 3 times.
        for i in range(4):
            rows += [shown(f"s{i}", "top"), rated(f"s{i}", "top", 1 if i < 2 else 0)]
        for i in range(3):
            rows += [shown(f"r{i}", "deep", "recall", "results", 8), rated(f"r{i}", "deep", 1 if i < 2 else -1)]
            rows += [shown(f"r{i}", "noise", "recall", "results", 9), rated(f"r{i}", "noise", 0)]
        table = {r["claim"]: r for r in usefulness.lifts(usefulness.showings(rows), usefulness.baselines(usefulness.showings(rows)), 3)}
        self.assertAlmostEqual(table["top"]["lift"], 0.0)
        self.assertGreater(table["deep"]["lift"], 0.3)
        self.assertEqual(table["deep"]["wrong"], 1)
        self.assertLess(table["noise"]["lift"], 0)

    def test_newest_rating_counts_and_unrated_or_unplaced_rows(self):
        old = {**shown("a", "c"), "metadata": "{}"}  # shown before rows kept their slot
        rows = [old, rated("a", "c", 0), rated("a", "c", 1), shown("b", "c"), rated("z", "c", 1)]
        got = usefulness.showings(rows)
        self.assertEqual([(s["slot"], s["score"]) for s in got],
                         [(("briefing", "?", "unplaced"), 1), (("briefing", "settled", "1-3"), None)])
        self.assertIn("2 showings, 1 rated (50%)", usefulness.report(rows, 1))

    def test_since_is_checked_before_it_reaches_sql(self):
        with self.assertRaises(SystemExit):
            usefulness.sql("2026-11-07' OR 1=1 --")


if __name__ == "__main__":
    unittest.main()
