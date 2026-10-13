"""Frozen cooperation contract and the MCP boundary around it.

The live local server stays the claim surface. append_context and query_context
exist here as schemas and validators, not as registered tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

from kypp.cooperation import (
    APPEND_CONTEXT_INPUT_SCHEMA,
    COOPERATION_LIMITS,
    QUERY_CONTEXT_INPUT_SCHEMA,
    CooperationError,
    canonical_json,
    client_payload_digest,
    compare_selection,
    hidden_predecessors,
    is_fresh,
    lineage_relation,
    seal_contribution,
    sha256_hex,
    stamp_record,
    utf8_bytes,
    validate_append_context,
    validate_contribution,
    validate_query_context,
    visible_to,
)
from kypp.mcp_server import build_mcp
from kypp.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "worker/test/fixtures/cooperation/v1.json").read_text())
BY_ID = {item["id"]: item for item in FIXTURE["cases"]}
LOCAL_TOOLS = (
    "observe",
    "claim",
    "recall",
    "expand",
    "briefing",
    "correct",
    "consolidate",
    "resolve_conflicts",
)


def _run(item):
    if item["op"] == "append":
        return validate_append_context(item["input"])
    return validate_query_context(item["input"])


def _names(schema):
    found = []

    def walk(node):
        if not isinstance(node, dict):
            return
        for key, value in (node.get("properties") or {}).items():
            found.append(key)
            walk(value)
        walk(node.get("items"))

    walk(schema)
    return found


class CooperationContractTest(unittest.TestCase):
    def test_limits_match_the_frozen_fixture(self):
        self.assertEqual(COOPERATION_LIMITS, FIXTURE["limits"])

    def test_canonical_vectors(self):
        for vector in FIXTURE["canonical_vectors"]:
            with self.subTest(vector["id"]):
                encoded = canonical_json(vector["value"])
                self.assertEqual(encoded, vector["canonical"])
                if "sha256" in vector:
                    self.assertEqual(sha256_hex(encoded), vector["sha256"])

    def test_fixture_cases(self):
        for item in FIXTURE["cases"]:
            with self.subTest(item["id"]):
                if item["expect"] == "accept":
                    validated = _run(item)
                    if item["op"] == "append":
                        self.assertEqual(client_payload_digest(validated), item["digest"])
                    continue
                with self.assertRaises(CooperationError) as caught:
                    _run(item)
                self.assertEqual(caught.exception.code, "INVALID_RECORD")

    def test_retry_digest_groups(self):
        same = [BY_ID[item]["digest"] for item in FIXTURE["digest_groups"]["same"]]
        self.assertEqual(same[0], same[1])
        different = [BY_ID[item]["digest"] for item in FIXTURE["digest_groups"]["different"]]
        self.assertNotEqual(different[0], different[1])

    def test_lineage_relations(self):
        for item in FIXTURE["lineage"]:
            with self.subTest(item["id"]):
                left = validate_append_context(BY_ID[item["left"]]["input"])
                right = validate_append_context(BY_ID[item["right"]]["input"])
                self.assertEqual(
                    lineage_relation(left["payload"], left["provenance"], right["payload"], right["provenance"]),
                    item["relation"],
                )

    def test_expiry_boundary(self):
        for item in FIXTURE["expiry"]:
            with self.subTest(item["id"]):
                self.assertEqual(is_fresh(item["expires_at"], item["now"]), item["fresh"])

    def test_stamp_is_principal_private(self):
        stamp = FIXTURE["visibility"]["stamp"]
        record = stamp_record({**stamp, "input": validate_append_context(BY_ID[stamp["case"]]["input"])})
        self.assertEqual(record["expires_at"], FIXTURE["visibility"]["expires_at"])
        self.assertIs(record["provenance"]["reported"], True)
        self.assertNotIn("authority", record)
        self.assertLessEqual(utf8_bytes(canonical_json(record)), COOPERATION_LIMITS["record_max_bytes"])
        for check in FIXTURE["visibility"]["checks"]:
            self.assertEqual(visible_to(record, check["principal"], check["origin"]), check["visible"])

    def test_hidden_predecessors(self):
        hidden = FIXTURE["hidden_predecessors"]
        self.assertEqual(hidden_predecessors(hidden["records"], hidden["cutoff"]), hidden["hidden"])

    def test_selection_order(self):
        from functools import cmp_to_key

        criteria = FIXTURE["selection"]["criteria"]
        ranked = sorted(
            FIXTURE["selection"]["records"],
            key=cmp_to_key(lambda left, right: compare_selection(left["record"], right["record"], criteria)),
        )
        self.assertEqual([item["id"] for item in ranked], FIXTURE["selection"]["order"])

    def test_contribution_budget(self):
        sealed = seal_contribution(FIXTURE["contribution"]["draft"])
        self.assertEqual(sha256_hex(canonical_json(sealed)), FIXTURE["contribution"]["sha256"])
        self.assertEqual(sealed["budget_bytes"], utf8_bytes(canonical_json(sealed)))
        self.assertEqual(validate_contribution(sealed)["budget_bytes"], sealed["budget_bytes"])

    def test_schemas_take_no_authority_parameter(self):
        self.assertIs(APPEND_CONTEXT_INPUT_SCHEMA["additionalProperties"], False)
        self.assertIs(QUERY_CONTEXT_INPUT_SCHEMA["additionalProperties"], False)
        self.assertEqual(sorted(APPEND_CONTEXT_INPUT_SCHEMA["properties"]), sorted(FIXTURE["append_properties"]))
        self.assertEqual(sorted(QUERY_CONTEXT_INPUT_SCHEMA["properties"]), sorted(FIXTURE["query_properties"]))
        names = _names(APPEND_CONTEXT_INPUT_SCHEMA) + _names(QUERY_CONTEXT_INPUT_SCHEMA)
        for forbidden in FIXTURE["forbidden_properties"]:
            self.assertNotIn(forbidden, names)
        base = dict(BY_ID["empty-handoff"]["input"])
        for field in ("authority", "verify", "measured", "capability", "approval"):
            with self.subTest(field):
                with self.assertRaises(CooperationError) as caught:
                    validate_append_context({**base, field: "x"})
                self.assertEqual(caught.exception.code, "INVALID_RECORD")
                self.assertIn(field, str(caught.exception))

    def test_nonfinite_numbers_fail_closed(self):
        for limit in (float("nan"), float("inf"), 1.5):
            with self.subTest(limit):
                with self.assertRaises(CooperationError) as caught:
                    validate_query_context({"schema_version": 1, "project": "kypp", "limit": limit})
                self.assertEqual(caught.exception.code, "INVALID_RECORD")

    def test_local_mcp_surface_is_not_expanded(self):
        os.environ.pop("TYPESAFE_API_KEY", None)
        os.environ.pop("KYPP_DECIDER_URL", None)
        os.environ["KYPP_S1"] = "off"
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = MemoryStore(str(Path(tmp.name) / "contract.db"))
        mcp = build_mcp(store, "pillbox", consumer="contract")
        tools = asyncio.run(mcp.list_tools())
        names = [tool.name for tool in tools]
        self.assertEqual(set(names), set(LOCAL_TOOLS))
        self.assertNotIn("append_context", names)
        self.assertNotIn("query_context", names)
        for tool in tools:
            props = (tool.inputSchema or {}).get("properties") or {}
            self.assertNotIn("verify", props, tool.name)
            self.assertNotIn("authority", props, tool.name)


if __name__ == "__main__":
    unittest.main()
