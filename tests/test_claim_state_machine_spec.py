"""Pure kernel tests; live writers remain legacy until coordinated cutover."""
from dataclasses import replace
from itertools import permutations, product
import json
from pathlib import Path
import unittest

from specs.claim_state_machine.model import (
    Accept, Actor, BeginCheck, Command, CompleteCheck, Correct, Propose, Reject,
    Refused, Stamp, State, transition,
)


AGENT = Actor("agent-client", "agent")
OPERATOR = Actor("owner", "operator")
MAINTENANCE = Actor("cron", "maintenance")


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.serial = 0

    def run_action(self, action, actor=OPERATOR, expected=None, command_id=None, stamp=None):
        self.serial += 1
        command = Command(command_id or f"cmd-{self.serial}",
                          self.state.revision if expected is None else expected, action)
        trusted = (stamp or Stamp()) if isinstance(action, Propose) else None
        self.state, receipt = transition(self.state, actor, command, stamp=trusted)
        self.invariants(self.state)
        return receipt

    def refuses(self, code, action, actor=OPERATOR, expected=None, stamp=None):
        before = self.state
        with self.assertRaises(Refused) as caught:
            self.run_action(action, actor, expected, stamp=stamp)
        self.assertEqual(caught.exception.code, code)
        self.assertIs(self.state, before)

    def invariants(self, state):
        self.assertEqual(len({c.id for c in state.claims}), len(state.claims))
        accepted = [c.id for c in state.claims if c.status == "accepted"]
        self.assertEqual(accepted, [] if state.head is None else [state.head])
        self.assertEqual([e.revision for e in state.events], list(range(1, state.revision + 1)))
        self.assertEqual(len(state.events), len(state.receipts))
        for claim in state.claims:
            if claim.status == "superseded":
                self.assertIsNotNone(claim.successor)
                self.assertNotEqual(claim.successor, claim.id)
                state.get(claim.successor)
            if claim.status in {"superseded", "rejected"}:
                self.assertFalse(claim.usable)

    def checked_claim(self):
        self.run_action(Propose("a", "lesson", verifier="operator-owned check"))
        token = self.run_action(BeginCheck("a")).token
        self.run_action(CompleteCheck(token, "passed"))

    def test_shared_transition_vectors(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] /
                              "specs/claim_state_machine/v1.json").read_text())
        actions = {"propose": Propose, "accept": Accept, "correct": Correct, "reject": Reject,
                   "begin_check": BeginCheck, "complete_check": CompleteCheck}
        actors = {"agent": AGENT, "operator": OPERATOR, "maintenance": MAINTENANCE,
                  "relay": Actor("relay", "agent")}
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                self.state = State()
                tokens = {}
                for step in case["steps"]:
                    payload = dict(step["payload"])
                    if "token_from" in payload:
                        payload["token"] = tokens[payload.pop("token_from")]
                    if "support" in payload:
                        payload["support"] = tuple(payload["support"])
                    action = actions[step["action"]](**payload)
                    stamp = Stamp(**step.get("trusted_stamp", {})) if isinstance(action, Propose) else None
                    if "error" in step:
                        self.refuses(step["error"], action, actors[step["actor"]], stamp=stamp)
                    else:
                        receipt = self.run_action(action, actors[step["actor"]], command_id=step.get("command_id"), stamp=stamp)
                        if receipt.token is not None:
                            tokens[step["command_id"]] = receipt.token
                    self.assertEqual(self.state.head, step["head"])
                    self.assertEqual({c.id: [c.status, c.authority, c.acceptance] for c in self.state.claims},
                                     step["claims"])
                    for cid, usable in step.get("usable", {}).items():
                        self.assertEqual(self.state.get(cid).usable, usable)

    def test_exact_ids_only_and_never_reuse_retired_id(self):
        self.run_action(Propose("abcdef012345", "first"), AGENT)
        self.refuses("not_found", Reject("abcdef01"))
        self.run_action(Reject("abcdef012345"))
        self.refuses("id_exists", Propose("abcdef012345", "replacement"), AGENT)

    def test_explicit_acceptance_is_not_human_endorsement(self):
        self.run_action(Propose("a", "lesson"), AGENT)
        self.run_action(Accept("a"))
        self.assertEqual((self.state.get("a").authority, self.state.get("a").acceptance), ("agent", "explicit"))

    def test_maintenance_rejection_cannot_withdraw_operator_admission(self):
        self.run_action(Propose("a", "lesson"), AGENT)
        self.run_action(Accept("a"))
        self.refuses("forbidden", Reject("a", "late S1 opinion"), MAINTENANCE)
        self.assertEqual(self.state.head, "a")
        self.assertTrue(self.state.get("a").usable)

    def test_late_maintenance_gate_only_rejects_unaccepted_candidates(self):
        self.run_action(Propose("a", "candidate"), AGENT)
        self.run_action(Reject("a", "control text"), MAINTENANCE)
        self.assertEqual(self.state.get("a").status, "rejected")

    def test_client_payload_cannot_set_source_or_echo(self):
        for field, value in (("source", "fake-session"), ("echo", False)):
            with self.subTest(field=field), self.assertRaises(TypeError):
                Propose(**{"claim_id": "a", "content": "lesson", field: value})
        with self.assertRaises(Refused) as caught:
            transition(self.state, AGENT, Command("no-stamp", 0, Propose("a", "lesson")))
        self.assertEqual(caught.exception.code, "missing_stamp")

    def test_proposals_and_agent_corrections_cannot_self_accept(self):
        self.run_action(Propose("a", "lesson"), AGENT)
        self.refuses("forbidden", Accept("a"), AGENT)
        self.refuses("forbidden", Correct("b", "human said"), AGENT)
        self.refuses("forbidden", Propose("c", "shell", verifier="true"), AGENT)
        self.refuses("forbidden", BeginCheck("a"), AGENT)

    def test_new_linked_assertion_does_not_inherit_privilege(self):
        self.run_action(Correct("a", "old"))
        self.run_action(Reject("a"))
        self.run_action(Propose("b", "new", previous="a"), AGENT)
        self.assertEqual((self.state.get("b").status, self.state.get("b").authority), ("candidate", "agent"))
        self.assertEqual(self.state.get("a").status, "rejected")

    def test_check_without_verifier_is_refused(self):
        self.run_action(Propose("a", "lesson"), AGENT)
        self.refuses("no_verifier", BeginCheck("a"))

    def test_passing_candidate_check_records_evidence_without_overriding_human(self):
        self.run_action(Correct("h", "human answer"))
        self.run_action(Propose("a", "different assertion", verifier="check"))
        token = self.run_action(BeginCheck("a")).token
        self.run_action(CompleteCheck(token, "passed"))
        claim = self.state.get("a")
        self.assertEqual((claim.status, claim.authority, claim.check), ("candidate", "agent", "passed"))
        self.assertEqual(self.state.head, "h")

    def test_reading_is_not_acceptance_or_a_review_decision(self):
        self.run_action(Propose("a", "lesson"), AGENT)
        before = self.state
        self.assertEqual(self.state.get("a").content, "lesson")
        self.assertIs(self.state, before)
        self.assertEqual(self.state.get("a").status, "candidate")

    def test_rejection_reason_is_audited(self):
        self.run_action(Propose("a", "lesson"), AGENT)
        self.run_action(Reject("a", "contradicted by current docs"))
        self.assertEqual(self.state.events[-1].reason, "contradicted by current docs")

    def test_check_gates_even_human_correction(self):
        self.run_action(Correct("a", "lesson", verifier="operator-owned check"))
        self.assertFalse(self.state.get("a").usable)
        acceptance = self.state.get("a").acceptance
        for outcome, usable in [("passed", True), ("failed", False), ("inconclusive", False), ("passed", True)]:
            token = self.run_action(BeginCheck("a")).token
            self.run_action(CompleteCheck(token, outcome))
            claim = self.state.get("a")
            self.assertEqual((claim.status, claim.authority, claim.acceptance, claim.usable),
                             ("accepted", "human", acceptance, usable))
        self.assertEqual([e.check_outcome for e in self.state.events if e.kind == "CompleteCheck"],
                         ["passed", "failed", "inconclusive", "passed"])

    def test_starting_check_retains_last_completed_verdict(self):
        self.checked_claim()
        self.run_action(BeginCheck("a"))
        self.assertTrue(self.state.get("a").usable)

    def test_supersession_terminal_at_every_authority(self):
        for authority in ("agent", "verified", "human"):
            with self.subTest(authority=authority):
                self.state = State()
                if authority == "human":
                    self.run_action(Correct("a", "old", verifier="check"))
                else:
                    self.run_action(Propose("a", "old", verifier="check"))
                    if authority == "verified":
                        token = self.run_action(BeginCheck("a")).token
                        self.run_action(CompleteCheck(token, "passed"))
                token = self.run_action(BeginCheck("a")).token
                self.run_action(Correct("b", "new"))
                self.refuses("retired", CompleteCheck(token, "passed"))
                self.refuses("retired", BeginCheck("a"))
                self.assertEqual(self.state.get("a").successor, "b")

    def test_explicit_rejection_is_terminal_despite_late_pass(self):
        self.checked_claim()
        token = self.run_action(BeginCheck("a")).token
        self.run_action(Reject("a"))
        self.refuses("retired", CompleteCheck(token, "passed"))
        self.refuses("retired", Accept("a"))

    def test_newer_attempt_fences_old_result(self):
        self.run_action(Propose("a", "lesson", verifier="check"))
        old = self.run_action(BeginCheck("a")).token
        new = self.run_action(BeginCheck("a")).token
        self.run_action(CompleteCheck(new, "failed"))
        self.refuses("stale_attempt", CompleteCheck(old, "passed"))
        self.assertEqual(self.state.get("a").check, "failed")

    def test_old_candidate_pass_does_not_replace_new_verified_head(self):
        self.run_action(Propose("a", "old", verifier="check a"))
        old = self.run_action(BeginCheck("a")).token
        self.run_action(Propose("b", "new", verifier="check b"))
        new = self.run_action(BeginCheck("b")).token
        self.run_action(CompleteCheck(new, "passed"))
        self.run_action(CompleteCheck(old, "passed"))
        self.assertEqual(self.state.head, "b")
        self.assertEqual((self.state.get("a").status, self.state.get("a").check), ("candidate", "passed"))
        self.assertEqual(self.state.get("a").authority, "agent")
        self.refuses("lower_authority", Accept("a"))
        # A deliberate fresh check, issued against the current head, can admit it.
        fresh = self.run_action(BeginCheck("a")).token
        self.run_action(CompleteCheck(fresh, "passed"))
        self.assertEqual((self.state.head, self.state.get("a").authority), ("a", "verified"))

    def test_no_head_to_no_head_does_not_reuse_old_candidate_generation(self):
        self.run_action(Propose("a", "old", verifier="check"))
        token = self.run_action(BeginCheck("a")).token
        self.run_action(Propose("b", "temporary"), AGENT)
        self.run_action(Accept("b"))
        self.run_action(Reject("b"))
        self.run_action(CompleteCheck(token, "passed"))
        self.assertIsNone(self.state.head)
        self.assertEqual(self.state.get("a").status, "candidate")

    def test_changed_verifier_digest_is_fenced(self):
        self.run_action(Propose("a", "lesson", verifier="check"))
        token = self.run_action(BeginCheck("a")).token
        self.refuses("stale_attempt", CompleteCheck(replace(token, verifier_digest="other"), "passed"))
        self.refuses("stale_attempt", CompleteCheck(replace(token, head_revision=99), "passed"))

    def test_conflicting_or_unknown_agreement_keeps_candidates(self):
        self.run_action(Propose("a", "port 1"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "port 2"), AGENT, stamp=Stamp("s2"))
        for agreement in ("exact", "conflicts", "unknown"):
            self.refuses("agreement_required", Accept("a", "recurrence", ("a", "b"), agreement), MAINTENANCE)
        self.assertEqual([c.status for c in self.state.claims], ["candidate", "candidate"])

    def test_echo_and_duplicate_source_do_not_count(self):
        self.run_action(Propose("a", "lesson"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "lesson"), AGENT, stamp=Stamp("s1"))
        self.refuses("insufficient_recurrence", Accept("a", "recurrence", ("a", "b"), "exact"), MAINTENANCE)
        self.run_action(Propose("c", "lesson"), AGENT, stamp=Stamp("s2", echo=True))
        self.refuses("invalid_support", Accept("a", "recurrence", ("a", "c"), "exact"), MAINTENANCE)

    def test_relay_by_another_principal_does_not_create_another_source(self):
        self.run_action(Propose("a", "lesson"), AGENT, stamp=Stamp("original-observation"))
        self.run_action(Propose("b", "lesson"), Actor("relay", "agent"), stamp=Stamp("original-observation"))
        self.refuses("insufficient_recurrence", Accept("a", "recurrence", ("a", "b"), "exact"), MAINTENANCE)

    def test_relay_cannot_remove_an_original_echo_marker(self):
        self.run_action(Propose("a", "lesson"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("echo", "lesson"), AGENT, stamp=Stamp("s2", echo=True))
        self.run_action(Propose("relay", "lesson"), Actor("relay", "agent"), stamp=Stamp("s2"))
        self.refuses("invalid_support", Accept("a", "recurrence", ("a", "relay"), "exact"), MAINTENANCE)

    def test_recurrence_keeps_evidence_after_superseding_duplicate(self):
        self.run_action(Propose("a", "lesson"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", " lesson "), AGENT, stamp=Stamp("s2"))
        self.run_action(Accept("a", "recurrence", ("a", "b"), "exact"), MAINTENANCE)
        self.assertEqual(self.state.get("a").authority, "agent")
        self.assertEqual(self.state.get("b").status, "superseded")
        self.assertEqual([e.source for e in self.state.events[-1].evidence], ["s1", "s2"])

    def test_retired_support_invalidates_plan(self):
        self.run_action(Propose("a", "lesson"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "lesson"), AGENT, stamp=Stamp("s2"))
        version = self.state.revision
        self.run_action(Reject("b"))
        action = Accept("a", "recurrence", ("a", "b"), "exact")
        self.refuses("stale", action, MAINTENANCE, version)
        self.refuses("invalid_support", action, MAINTENANCE)

    def test_lower_authority_cannot_replace_unconfirmed_human_head(self):
        self.run_action(Correct("h", "human", verifier="check"))
        self.run_action(Propose("a", "guess"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "guess"), AGENT, stamp=Stamp("s2"))
        self.refuses("lower_authority", Accept("a", "recurrence", ("a", "b"), "exact"), MAINTENANCE)
        self.assertEqual(self.state.head, "h")
        self.assertFalse(self.state.get("h").usable)

    def test_old_pending_candidates_cannot_replace_new_head(self):
        self.run_action(Propose("a", "old guess"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "old guess"), AGENT, stamp=Stamp("s2"))
        self.run_action(Propose("new", "current answer"), AGENT)
        self.run_action(Accept("new"))
        self.refuses("fresh_support_required", Accept("a", "recurrence", ("a", "b"), "exact"), MAINTENANCE)

    def test_new_observation_can_reuse_older_pending_agreement(self):
        self.run_action(Propose("x", "port 1"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("y", "port 1"), AGENT, stamp=Stamp("s2"))
        self.run_action(Propose("z", "port 2"), AGENT, stamp=Stamp("s3"))
        self.run_action(Accept("x", "recurrence", ("x", "y"), "exact"), MAINTENANCE)
        self.run_action(Propose("w", "port 2"), AGENT, stamp=Stamp("s4"))
        self.run_action(Accept("z", "recurrence", ("z", "w"), "exact"), MAINTENANCE)
        self.assertEqual(self.state.head, "z")
        self.assertEqual(self.state.get("x").successor, "z")
        self.assertEqual([e.source for e in self.state.events[-1].evidence], ["s3", "s4"])

    def test_relaying_old_sources_cannot_refresh_their_generation(self):
        self.run_action(Propose("a", "old guess"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "old guess"), AGENT, stamp=Stamp("s2"))
        self.run_action(Propose("head", "current answer"), AGENT)
        self.run_action(Accept("head"))
        self.run_action(Propose("relay", "old guess"), Actor("relay", "agent"), stamp=Stamp("s2"))
        self.refuses("fresh_support_required", Accept("a", "recurrence", ("a", "relay"), "exact"), MAINTENANCE)

    def test_idempotent_receipt_survives_later_mutations(self):
        command = Command("request", 0, Propose("a", "lesson"))
        self.state, original = transition(self.state, AGENT, command, stamp=Stamp("s1"))
        self.run_action(Correct("h", "human"))
        before = self.state
        self.state, replay = transition(self.state, AGENT, command)
        self.assertIs(self.state, before)
        self.assertEqual(original, replay)
        unchanged, replay = transition(self.state, AGENT, command, stamp=Stamp("new-session", echo=True))
        self.assertIs(unchanged, before)
        self.assertEqual(unchanged.get("a").source, "s1")
        self.assertFalse(unchanged.get("a").echo)
        unchanged, replay = transition(self.state, AGENT, command, k=3)
        self.assertIs(unchanged, before)
        self.assertEqual(original, replay)
        with self.assertRaises(Refused) as caught:
            transition(self.state, AGENT, replace(command, action=Propose("a", "different")))
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_same_count_replacement_cannot_reuse_old_group_version(self):
        self.run_action(Propose("a", "lesson"), AGENT, stamp=Stamp("s1"))
        self.run_action(Propose("b", "lesson"), AGENT, stamp=Stamp("s2"))
        version = self.state.revision
        self.run_action(Reject("a"))
        self.run_action(Propose("c", "lesson"), AGENT, stamp=Stamp("s3"))
        self.refuses("stale", Accept("a", "recurrence", ("a", "b"), "exact"), MAINTENANCE, version)

    def test_all_short_check_result_interleavings(self):
        # Two completions and a correction in every order, with fresh group reads.
        for schedule in permutations(("old_pass", "new_fail", "correct")):
            with self.subTest(schedule=schedule):
                self.state = State()
                self.run_action(Propose("a", "old", verifier="check"))
                old = self.run_action(BeginCheck("a")).token
                new = self.run_action(BeginCheck("a")).token
                for item in schedule:
                    action = Correct("b", "new") if item == "correct" else CompleteCheck(
                        old if item == "old_pass" else new, "passed" if item == "old_pass" else "failed")
                    try:
                        self.run_action(action)
                    except Refused as error:
                        self.assertIn(error.code, {"stale_attempt", "retired"})
                self.assertEqual(self.state.head, "b")
                self.assertEqual(self.state.get("a").status, "superseded")

    def test_bounded_sequences_preserve_terminal_states_and_body(self):
        # 7^4 command sequences; invalid operations must leave the whole state unchanged.
        actions = (Propose("a", "one", verifier="check"), Accept("a"), Reject("a"),
                   Correct("b", "two"), BeginCheck("a"), Reject("b"), Propose("a", "reuse"))
        for schedule in product(actions, repeat=4):
            state = State()
            for index, action in enumerate(schedule):
                before = state
                try:
                    state, _ = transition(state, OPERATOR, Command(f"c{index}", state.revision, action),
                                          stamp=Stamp() if isinstance(action, Propose) else None)
                except Refused:
                    self.assertIs(state, before)
                self.invariants(state)
                for old in before.claims:
                    current = state.get(old.id)
                    self.assertEqual(current.content, old.content)
                    self.assertEqual(current.verifier, old.verifier)
                    if old.status in {"superseded", "rejected"}:
                        self.assertEqual(current, old)


if __name__ == "__main__":
    unittest.main()
