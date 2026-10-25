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

    def claim(self, subject, content, session, scope="project", project="p", accept=False):
        # One observation per session, as distill_session files it; corroboration counts those.
        if session not in self.obs:
            self.obs[session] = self.store.observe("distill", "trace", project="p", source=f"session:{session}")
        oid = self.obs[session]
        return self.store.claim("pitfall", subject, content, scope=scope, project=project, source_ids=[oid],
                                accept=accept)

    def live(self):
        return {c.id for c in self.store.live_claims("p", None)}

    def run_gate(self, judge, tag=lambda c: "w2v"):
        return consolidate(self.store, project="p", tag_of=tag, pair_judge=judge)

    def test_agreeing_restatements_from_two_sessions_promote_both_and_retire_neither(self):
        a = self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        b = self.claim("Gensim Word2Vec on an empty corpus", EMPTY + " (gensim 4)", "s2")
        res = self.run_gate(lambda pair: 0.95)
        self.assertEqual((res["promoted"], res["superseded"]), (2, 0))
        statuses = {c.id: c.status for c in self.store.live_claims("p", None)}
        self.assertEqual(statuses, {a: "accepted", b: "accepted"})

    def test_a_second_pass_changes_nothing(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2")
        self.run_gate(lambda pair: 0.95)
        res = self.run_gate(lambda pair: 0.95)
        self.assertEqual((res["promoted"], res["superseded"]), (0, 0))

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
        # A~B and B~C, but A and C are different lessons: only the centre's direct matches count.
        a = self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        b = self.claim("Word2Vec edge cases", EMPTY + "; also pass min_count=1", "s2")
        c = self.claim("Word2Vec min_count drops rare words", "Pass min_count=1", "s3")
        direct = {frozenset((a, b)), frozenset((b, c))}
        res = self.run_gate(lambda pair: 0.95 if frozenset(x.id for x in pair) in direct else 0.1)
        live = {x.id: x.status for x in self.store.live_claims("p", None)}
        self.assertEqual(len(live), 3)  # nothing is retired
        # a and c only link through b, so they are accepted together only in a b-centred cluster.
        for p in res["plan"]:
            if {a, c} <= set(p["agreed"]):
                self.assertEqual(p["survivor"], b)

    def test_a_failed_centre_leaves_its_matches_for_another(self):
        # K=3: A matches only B, so an A-centred cluster fails; B matches C and D and still wins.
        a = self.claim("A", EMPTY, "s1")
        b = self.claim("B", EMPTY, "s2")
        c = self.claim("C", EMPTY, "s3")
        d = self.claim("D", EMPTY, "s4")
        direct = {frozenset(p) for p in ((a, b), (b, c), (b, d))}
        res = consolidate(self.store, project="p", tag_of=lambda x: "w2v", accept_corroboration=3,
                          pair_judge=lambda pair: 0.95 if frozenset(x.id for x in pair) in direct else 0.1)
        self.assertEqual([(p["survivor"], sorted(p["agreed"])) for p in res["plan"]], [(b, sorted((a, b, c, d)))])

    def test_tags_never_cross_projects(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1", project="p")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2", project="q")
        res = consolidate(self.store, project=None, tag_of=lambda c: "w2v", pair_judge=lambda pair: 0.99)
        self.assertEqual(res["promoted"], 0)

    def test_pending_updates_stay_with_the_exact_subject_pass(self):
        # An accepted answer plus a later candidate on the same subject: the agreement phase must not
        # accept the update alongside the old answer.
        self.claim("Word2Vec empty corpus", "old answer", "s0", accept=True)
        self.claim("Word2Vec empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2")
        self.assertEqual(self.run_gate(lambda pair: 0.99)["promoted"], 0)
        accepted = [c.content for c in self.store.live_claims("p", None) if c.status == "accepted"]
        self.assertEqual(accepted, ["old answer"])

    def test_without_the_phase_behaviour_is_unchanged(self):
        self.claim("Word2Vec crashes on empty corpus", EMPTY, "s1")
        self.claim("Gensim Word2Vec on an empty corpus", EMPTY, "s2")
        self.assertEqual(consolidate(self.store, project="p")["promoted"], 0)


if __name__ == "__main__":
    unittest.main()
