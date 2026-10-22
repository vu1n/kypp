"""The agreement phase of consolidate: restatements of one lesson under different subjects.

A tag only narrows which claims are compared; the pair judge's affirmative answer is what joins
them, and recurrence across sessions is still what promotes.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from kypp.arbiter import consolidate
from kypp.store import MemoryStore

EMPTY = "Word2Vec on an empty corpus raises; return an untrained model instead"


class AgreementGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(os.path.join(self.tmp.name, "m.db"))
        self.obs = {}

    def tearDown(self):
        self.tmp.cleanup()

    def claim(self, subject, content, session):
        # One observation per session, as distill_session files it; corroboration counts those.
        if session not in self.obs:
            self.obs[session] = self.store.observe("distill", "trace", project="p", source=f"session:{session}")
        oid = self.obs[session]
        return self.store.claim("pitfall", subject, content, scope="project", project="p", source_ids=[oid])

    def live(self):
        return {c.id for c in self.store.live_claims("p", None)}

    def run_gate(self, judge, tag=lambda c: "w2v"):
        return consolidate(self.store, project="p", tag_of=tag, pair_judge=judge)

    def test_agreeing_restatements_from_two_sessions_promote_one(self):
        a = self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        b = self.claim("Gensim Word2Vec on an empty corpus", EMPTY + " (gensim 4)", "s2")
        res = self.run_gate(lambda pair: 0.95)
        self.assertEqual(res["promoted"], 1)
        statuses = {c.id: c.status for c in self.store.live_claims("p", None)}
        self.assertEqual(sorted(statuses.values()), ["accepted"])
        self.assertTrue(set(statuses) <= {a, b})

    def test_a_shared_tag_alone_never_corroborates(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Word2Vec min_count drops rare words", "Pass min_count=1", "s2")
        self.assertEqual(self.run_gate(lambda pair: 0.2)["promoted"], 0)

    def test_no_opinion_joins_nothing(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2")
        self.assertEqual(self.run_gate(lambda pair: None)["promoted"], 0)

    def test_one_session_cannot_agree_with_itself(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s1")
        self.assertEqual(self.run_gate(lambda pair: 0.99)["promoted"], 0)

    def test_untagged_claims_are_not_compared(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2")
        self.assertEqual(self.run_gate(lambda pair: 0.99, tag=lambda c: None)["promoted"], 0)

    def test_a_transitive_link_is_not_agreement(self):
        # A~B and B~C, but A and C are different lessons: only the survivor's direct matches merge.
        a = self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        b = self.claim("Word2Vec edge cases", EMPTY + "; also pass min_count=1", "s2")
        c = self.claim("Word2Vec min_count drops rare words", "Pass min_count=1", "s3")
        direct = {frozenset((a, b)), frozenset((b, c))}
        self.run_gate(lambda pair: 0.95 if frozenset(x.id for x in pair) in direct else 0.1)
        live = {x.id: x.status for x in self.store.live_claims("p", None)}
        self.assertEqual(sum(s == "accepted" for s in live.values()), 1)
        self.assertGreaterEqual(len(live), 2)  # the lesson that only linked through b survives

    def test_without_the_phase_behaviour_is_unchanged(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2")
        self.assertEqual(consolidate(self.store, project="p")["promoted"], 0)


if __name__ == "__main__":
    unittest.main()
