"""Survivor selection against an oracle taken from the authority-order bullets.

Authority beats conflicting confidence, status, evidence, and recency. Argument
order does not change a strict winner. A confidence-only early return does not
match the oracle.
"""

from __future__ import annotations

import unittest

from kypp.arbiter import _survivor
from kypp.store import Claim


def claim(**over) -> Claim:
    fields = dict(
        id="a",
        type="fact",
        subject="s",
        content="c",
        scope="project",
        status="candidate",
        confidence=0.5,
        source_ids=[],
        authority="agent",
        updated_at="2020-01-01T00:00:00",
    )
    fields.update(over)
    return Claim(**fields)


def oracle(members: list[Claim]) -> Claim:
    """agent < verified < human, then accepted, confidence, evidence, recency."""
    rank = {"agent": 0, "verified": 1, "human": 2}

    def key(item: Claim):
        return (
            rank[item.authority],
            item.status == "accepted",
            item.confidence or 0,
            len(item.source_ids),
            item.updated_at,
        )

    return max(members, key=key)


def early_return(members: list[Claim]) -> Claim:
    """The mutation: confidence decides before authority is considered."""
    return max(members, key=lambda item: item.confidence or 0)


class RankOracleTest(unittest.TestCase):
    def test_authority_beats_confidence_status_evidence_and_recency(self):
        human = claim(
            id="human",
            authority="human",
            confidence=0.1,
            status="candidate",
            source_ids=[],
            updated_at="2019-01-01T00:00:00",
        )
        agent = claim(
            id="agent",
            authority="agent",
            confidence=0.99,
            status="accepted",
            source_ids=["s1", "s2", "s3"],
            updated_at="2026-01-01T00:00:00",
        )
        for members in ((agent, human), (human, agent)):
            self.assertEqual(_survivor(list(members)).id, oracle(list(members)).id)
            self.assertEqual(oracle(list(members)).id, "human")
        self.assertNotEqual(early_return([agent, human]).id, oracle([agent, human]).id)

    def test_later_keys_break_ties_inside_one_authority(self):
        weaker = claim(id="weak", authority="verified", status="candidate", confidence=0.9, updated_at="2026")
        stronger = claim(
            id="strong",
            authority="verified",
            status="accepted",
            confidence=0.2,
            source_ids=["only"],
            updated_at="2020",
        )
        for members in ((weaker, stronger), (stronger, weaker)):
            self.assertEqual(_survivor(list(members)).id, "strong")
            self.assertEqual(_survivor(list(members)).id, oracle(list(members)).id)

    def test_every_strict_pair_beats_each_secondary_key(self):
        pairs = (("human", "verified"), ("human", "agent"), ("verified", "agent"))
        edges = (
            {"confidence": 0.99},
            {"status": "accepted"},
            {"source_ids": ["s1", "s2", "s3"]},
            {"updated_at": "2026-01-01T00:00:00"},
        )
        for higher, lower in pairs:
            for edge in edges:
                low_fields = dict(
                    id=lower,
                    authority=lower,
                    confidence=0.1,
                    status="candidate",
                    source_ids=[],
                    updated_at="2019-01-01T00:00:00",
                )
                low_fields.update(edge)
                low = claim(**low_fields)
                high = claim(
                    id=higher,
                    authority=higher,
                    confidence=0.1,
                    status="candidate",
                    source_ids=[],
                    updated_at="2019-01-01T00:00:00",
                )
                for members in ((low, high), (high, low)):
                    self.assertEqual(_survivor(list(members)).id, higher, (higher, lower, edge))
                    self.assertEqual(oracle(list(members)).id, higher, (higher, lower, edge))
                if edge.get("confidence", 0.1) != 0.1:
                    self.assertNotEqual(early_return([low, high]).id, oracle([low, high]).id)

    def test_equal_keys_are_a_tie(self):
        left = claim(id="left")
        right = claim(id="right")
        self.assertIn(_survivor([left, right]).id, {"left", "right"})
        self.assertIn(_survivor([right, left]).id, {"left", "right"})
        self.assertIn(oracle([left, right]).id, {"left", "right"})
        self.assertIn(oracle([right, left]).id, {"left", "right"})


if __name__ == "__main__":
    unittest.main()
