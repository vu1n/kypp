"""Expand portable vectors using the pure Python kernel (stdlib only).

The Worker test invokes this script to compare complete persisted state, including
evidence, attempt tokens, event ordering and byte-identical request fingerprints.
Existing vector assertions remain the independent expected lifecycle outcomes.
"""
from dataclasses import asdict
from itertools import permutations
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kypp.claim_machine import (
    Accept, Actor, BeginCheck, Command, CompleteCheck, Correct, Propose, Reject,
    Refused, Stamp, State, transition,
)

ACTIONS = {"propose": Propose, "accept": Accept, "correct": Correct, "reject": Reject,
           "begin_check": BeginCheck, "complete_check": CompleteCheck}
ACTORS = {"agent": Actor("agent-client", "agent"), "operator": Actor("owner", "operator"),
          "maintenance": Actor("cron", "maintenance"), "relay": Actor("relay", "agent")}


def vectors():
    fixture = json.loads((Path(__file__).resolve().parents[1] / "specs/claim_state_machine/v1.json").read_text())
    for case in fixture["cases"]:
        state, tokens, steps = State(), {}, []
        for i, step in enumerate(case["steps"]):
            payload = dict(step["payload"])
            if "token_from" in payload:
                payload["token"] = tokens[payload.pop("token_from")]
            if "support" in payload:
                payload["support"] = tuple(payload["support"])
            action = ACTIONS[step["action"]](**payload)
            actor = ACTORS[step["actor"]]
            command = Command(step.get("command_id", f"cmd-{i}"), state.revision, action)
            stamp = Stamp(**step.get("trusted_stamp", {})) if isinstance(action, Propose) else None
            result = {}
            try:
                state, receipt = transition(state, actor, command, stamp=stamp)
                assert "error" not in step, step
                result["receipt"] = receipt
                if receipt.token:
                    tokens[command.id] = receipt.token
            except Refused as error:
                assert error.code == step.get("error"), (step, error.code)
                result["error"] = error.code
            assert state.head == step["head"]
            assert {c.id: [c.status, c.authority, c.acceptance] for c in state.claims} == step["claims"]
            for cid, usable in step.get("usable", {}).items():
                assert state.get(cid).usable == usable
            steps.append({"actor": actor, "command": command, "stamp": stamp, "state": state, **result})
        yield {"name": case["name"], "steps": steps}


def derived_vectors():
    """Implementation-port checks, not additional independent specifications."""
    for order in permutations(["a", "b", "fix"]):
        state, tokens, steps = State(), {}, []
        initial = [Propose("a", "old", verifier="check a"), BeginCheck("a"),
                   Propose("b", "other", verifier="check b"), BeginCheck("b")]
        for i, item in enumerate([*initial, *order]):
            action = (Correct("human", "right") if item == "fix" else CompleteCheck(tokens[item], "passed")) if isinstance(item, str) else item
            command = Command(f"cmd-{i}", state.revision, action)
            stamp = Stamp() if isinstance(action, Propose) else None
            result = {}
            try:
                state, receipt = transition(state, ACTORS["operator"], command, stamp=stamp)
                result["receipt"] = receipt
                if receipt.token:
                    tokens[receipt.claim_id] = receipt.token
            except Refused as error:
                result["error"] = error.code
            steps.append({"actor": ACTORS["operator"], "command": command, "stamp": stamp, "state": state, **result})
        assert state.head == "human"
        yield {"name": "completion/correction order " + ",".join(order), "steps": steps}

    # Python and JavaScript disagree about native whitespace sets. Check every
    # Python whitespace plus BOM (which Python does NOT consider whitespace).
    spaces = [chr(i) for i in range(0x3100) if chr(i).isspace()] + ["\ufeff"]
    for space in spaces:
        state, steps = State(), []
        actions = [Propose("a", f"port{space}1"), Propose("b", "port 1"),
                   Accept("a", "recurrence", ("a", "b"), "exact")]
        for i, action in enumerate(actions):
            actor = ACTORS["maintenance"] if isinstance(action, Accept) else ACTORS["agent"]
            stamp = Stamp(f"session-{i}") if isinstance(action, Propose) else None
            command = Command(f"cmd-{i}", state.revision, action)
            result = {}
            try:
                state, receipt = transition(state, actor, command, stamp=stamp)
                result["receipt"] = receipt
            except Refused as error:
                result["error"] = error.code
            steps.append({"actor": actor, "command": command, "stamp": stamp, "state": state, **result})
        assert state.head == (None if space == "\ufeff" else "a")
        yield {"name": f"exact agreement whitespace U+{ord(space):04X}", "steps": steps}


if __name__ == "__main__":
    cases = [*vectors(), *derived_vectors()]
    def encode(value):
        if isinstance(value, Command):
            return {**asdict(value), "action": {"kind": type(value.action).__name__, **asdict(value.action)}}
        return asdict(value)
    print(json.dumps(cases, default=encode, ensure_ascii=False))
