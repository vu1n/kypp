"""Synthetic preservation/identity oracle; no real store or production migration.

Expectations were hand authored after source review, not blind holdouts. This is
an independent specification plus characterization of the existing boundaries.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from kypp.arbiter import _rank
from kypp.claim_commands import Group
from kypp.claim_machine import Refused
from specs.claim_state_machine.cutover import canonical_group, inventory

VECTORS = json.loads((Path(__file__).parents[1] /
                     "specs/claim_state_machine/cutover-v1.json").read_text())


def row(id, **overrides):
    return {"id": id, "type": "fact", "subject": "port", "content": f"answer {id}",
            "scope": "global", "project": "repo-a", "origin": "original-origin",
            "user": "writer", "agent": "model", "session": "session-s",
            "status": "accepted", "authority": "human", "confidence": 0.5,
            "source_ids": '["observation-1"]', "verify": "test -f marker",
            "created_at": "2026-10-15T00:00:00Z", "updated_at": "2026-10-17T00:00:00Z",
            "metadata": '{"s1":{"keep":0.9},"unknown_future_field":true}',
            "code_refs": [{"path": "src/a.py", "symbol": "thing"}],
            "embedding": b"\x00\xff", "valid_from": "old", "valid_to": None, **overrides}


class CutoverSpecTests(unittest.TestCase):
    def test_canonical_keys_match_hand_authored_cases_and_staged_encoding(self):
        keys = []
        for case in VECTORS["groups"]:
            key = canonical_group(case["legacy"])
            self.assertEqual(list(key), case["canonical"])
            self.assertEqual(json.loads(Group(*key).key), case["canonical"])
            keys.append(key)
        self.assertEqual(keys[0], keys[1])  # global coalesces BEFORE head selection
        self.assertEqual(len(set(keys[2:])), 6)  # owner, audience and project stay distinct

    def test_unsorted_is_preserved_but_has_no_staged_arbitration_group(self):
        old = row("u", **VECTORS["unplaced"], status="candidate")
        self.assertIsNone(canonical_group(old))
        result = inventory([old])
        self.assertEqual(result["unplaced"], ["u"])
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["archive"]["claims"], [old])
        with self.assertRaises(Refused) as caught:
            _ = Group("port", "project").key
        self.assertEqual(caught.exception.code, "invalid_group")

    def test_missing_owner_and_group_are_reported_not_guessed(self):
        rows = [row(str(i), **fields) for i, fields in enumerate(VECTORS["unsupported"])]
        result = inventory(rows)
        self.assertEqual(result["archive"]["claims"], rows)
        self.assertEqual([x["id"] for x in result["unsupported"]], ["0", "1", "2"])
        self.assertEqual(result["groups"], [])

    def test_duplicate_global_heads_coalesce_with_stable_id_tie_break(self):
        rows = [row("a"), row("b", project="repo-b"),
                row("newer-agent", authority="agent", confidence=1, updated_at="2026-10-18T00:00:00Z"),
                row("pending", status="candidate", confidence=1),
                row("retired", status="rejected", confidence=1),
                row("historical", status="superseded", confidence=1)]
        expected = {"key": ["port", "global", None, None], "head": "b",
                    "members": ["a", "b", "historical", "newer-agent", "pending", "retired"],
                    "supersede": ["a", "newer-agent"], "acceptance": "legacy_unknown", "check": None}
        for ordered in (rows, list(reversed(rows))):
            self.assertEqual(inventory(ordered)["groups"], [expected])

    def test_candidates_and_retired_history_never_create_a_head(self):
        rows = [row("pending", status="candidate"), row("r", status="rejected"),
                row("s", status="superseded")]
        group = inventory(rows)["groups"][0]
        self.assertIsNone(group["head"])
        self.assertIsNone(group["acceptance"])
        self.assertEqual(group["supersede"], [])

    def test_raw_archive_retains_every_field_and_pointer_namespace(self):
        rows = [row("a"), row("old", status="superseded")]
        namespace = "repo-a"
        pointers = [{"id": "p", "claim_id": "a", "entry_ref": f"doc://{namespace}/features@0001#x",
                     "project": "repo-a", "path": "src/a.py"},
                    {"id": "standalone", "claim_id": "", "project": "another-doc-namespace"}]
        before = deepcopy({"claims": rows, "observations": [], "pointers": pointers})
        result = inventory(rows, pointers=pointers)
        self.assertEqual(result["archive"], before)
        self.assertEqual(result["archive"]["claims"][0]["embedding"], b"\x00\xff")
        rows[0]["code_refs"][0]["path"] = "mutated-after-snapshot"
        pointers[0]["project"] = "mutated-after-snapshot"
        self.assertEqual(result["archive"], before)

    def test_replayed_capture_identity_is_not_fresh_recurrence(self):
        observations = [{"id": "o1", "source": "session:s", "metadata": "first capture"},
                        {"id": "o2", "source": "session:s", "metadata": "marker-crash replay"},
                        {"id": "unknown", "source": None}]
        rows = [row("a", source_ids=["o1"]), row("b", source_ids=["o2"])]
        result = inventory(rows, observations)
        self.assertEqual(result["recorded_source_aliases"], {"session:s": ["o1", "o2"]})
        self.assertEqual(result["recurrence_sources"], [])
        self.assertEqual(result["archive"]["observations"], observations)
        self.assertEqual(result["archive"]["claims"], rows)

    def test_legacy_rank_matches_independent_portable_cells(self):
        for case in VECTORS["rank"]:
            self.assertEqual(list(_rank(SimpleNamespace(**case))), case["expected"])

    def test_rank_uses_decoded_source_count_without_rewriting_raw_json(self):
        rows = [row("z", source_ids='["a-very-long-observation-id"]'),
                row("a", source_ids='["x", "y"]')]
        result = inventory(rows)
        self.assertEqual(result["groups"][0]["head"], "a")
        self.assertEqual(result["archive"]["claims"], rows)

    def test_ambiguous_ids_or_unrecognized_lifecycle_authority_stop_inventory(self):
        for rows in ([row("a"), row("a")], [row("a", authority="unknown")],
                     [row("a", status="archived")]):
            before = deepcopy(rows)
            with self.assertRaises(ValueError):
                inventory(rows)
            self.assertEqual(rows, before)
