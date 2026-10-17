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

from jsonschema import Draft7Validator

from kypp.cooperation import (
    APPEND_CONTEXT_INPUT_SCHEMA,
    COOPERATION_BROWSE_SQL,
    COOPERATION_EXACT_SQL,
    COOPERATION_FTS_MATCH_SQL,
    COOPERATION_LIMITS,
    QUERY_CONTEXT_INPUT_SCHEMA,
    CooperationError,
    canonical_json,
    client_payload_digest,
    fts_query,
    hidden_predecessors,
    is_fresh,
    lineage_relation,
    record_revision,
    reference_search,
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
DIAGNOSTICS = json.loads((ROOT / "worker/test/fixtures/cooperation/diagnostics.json").read_text())
REGRESSIONS = json.loads((ROOT / "worker/test/fixtures/cooperation/regressions.json").read_text())
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
        for key in ("items", "then", "else"):
            walk(node.get(key))
        for key in ("oneOf", "anyOf", "allOf"):
            for item in node.get(key) or []:
                walk(item)

    walk(schema)
    return found


def _search(records, **request):
    base = {
        "principal": "owner",
        "origin": "kypp",
        "now": "2026-10-13T12:00:00Z",
        "cutoff": "2026-10-13T12:00:00Z",
        "query": "",
        "repository": None,
        "candidate_revision": None,
        "kinds": None,
        "window": COOPERATION_LIMITS["candidate_window"],
    }
    base.update(request)
    return reference_search(records, base)


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
        criteria = FIXTURE["selection"]["criteria"]
        records = [item["record"] for item in FIXTURE["selection"]["records"]]
        ranked = _search(records, query=criteria["query"], repository=criteria["repository"], candidate_revision=criteria["candidate_revision"])
        names = {item["record"]["id"]: item["id"] for item in FIXTURE["selection"]["records"]}
        self.assertEqual([names[item] for item in ranked["ids"]], FIXTURE["selection"]["order"])
        self.assertNotIn("3123456789abcdef0123456789abcdef", ranked["ids"])

    def test_fts_query_quotes_tokens(self):
        self.assertEqual(fts_query(""), "")
        self.assertEqual(fts_query("*"), "")
        self.assertEqual(fts_query('"'), "")
        self.assertEqual(fts_query("lexical-hit"), '"lexical" OR "hit"')
        self.assertEqual(fts_query("NOT alpha"), '"NOT" OR "alpha"')
        self.assertEqual(fts_query("body:secret"), '"body" OR "secret"')
        self.assertEqual(fts_query("café"), '"café"')

    def test_invisible_match_is_not_counted(self):
        visible = json.loads(json.dumps(FIXTURE["selection"]["records"][2]["record"]))
        hidden = json.loads(json.dumps(visible))
        hidden["id"] = "4123456789abcdef0123456789abcdef"
        hidden["author_principal"] = "other"
        hidden["audience"] = {"kind": "principal", "principal": "other"}
        hidden["payload"]["summary"] = "lexical-hit lexical-hit lexical-hit lexical-hit"
        ranked = _search([visible, hidden], query="lexical-hit", window=1)
        self.assertEqual(ranked["ids"], [visible["id"]])
        self.assertIs(ranked["window_capped"], False)

    def test_unicode_marks_preserve_fts_matches(self):
        stamp = FIXTURE["visibility"]["stamp"]
        for case in REGRESSIONS["unicode_search"]:
            with self.subTest(case["id"]):
                self.assertEqual(fts_query(case["query"]), case["match"])
                record = stamp_record({**stamp, "input": validate_append_context(BY_ID[stamp["case"]]["input"])})
                record["payload"]["summary"] = case["text"]
                ranked = _search([record], query=case["query"], principal=record["author_principal"],
                                 origin=record["origin"], now=record["created_at"], cutoff=record["created_at"])
                self.assertEqual(ranked["ids"], [record["id"]])
                self.assertEqual(ranked["fts_rows"], 1)

    def test_older_relevant_record_is_findable(self):
        old = json.loads(json.dumps(FIXTURE["selection"]["records"][0]["record"]))
        old["id"] = "5123456789abcdef0123456789abcdef"
        old["created_at"] = "2020-01-01T00:00:00Z"
        old["expires_at"] = "2030-01-01T00:00:00Z"
        old["work_ref"]["repository"] = "https://github.com/vu1n/other"
        old["work_ref"]["candidate_revision"] = "c" * 40
        old["payload"]["summary"] = "uniquealpha uniquealpha uniquealpha"
        newer = []
        for index in range(100):
            row = json.loads(json.dumps(FIXTURE["selection"]["records"][3]["record"]))
            row["id"] = f"{index:032x}"
            row["created_at"] = "2026-10-13T05:00:00Z"
            row["payload"]["summary"] = f"noise{index}"
            newer.append(row)
        ranked = _search([old, *newer], query="uniquealpha")
        self.assertEqual(ranked["ids"], [old["id"]])

    def test_hostile_match_syntax_stays_inside_the_audience(self):
        rows = [item["record"] for item in FIXTURE["selection"]["records"]]
        hidden = json.loads(json.dumps(rows[0]))
        hidden["id"] = "6123456789abcdef0123456789abcdef"
        hidden["author_principal"] = "other"
        hidden["audience"] = {"kind": "principal", "principal": "other"}
        hidden["payload"]["summary"] = "secret"
        for query in ("*", '"', "NOT secret", "body:secret", "NEAR(secret *)"):
            ranked = _search([*rows, hidden], query=query)
            self.assertNotIn(hidden["id"], ranked["ids"], query)
        names = {item["record"]["id"]: item["id"] for item in FIXTURE["selection"]["records"]}
        both = _search(rows, query="plain NOT lexical-hit")
        self.assertEqual(sorted(names[item] for item in both["ids"]), ["s-exact-lexical", "s-exact-plain", "s-lexical", "s-neither"])

    def test_each_statement_stops_at_the_window_plus_one(self):
        self.assertIn("ORDER BY exact DESC, score ASC, r.id ASC\nLIMIT :limit", COOPERATION_FTS_MATCH_SQL)
        self.assertIn("ORDER BY r.id ASC\nLIMIT :limit", COOPERATION_EXACT_SQL)
        self.assertTrue(COOPERATION_BROWSE_SQL.endswith("LIMIT :limit"))
        base = json.loads(json.dumps(FIXTURE["selection"]["records"][3]["record"]))
        lexical = []
        for index in range(150):
            row = json.loads(json.dumps(base))
            row["id"] = f"{index:032x}"
            row["work_ref"]["repository"] = f"https://github.com/vu1n/noise{index}"
            row["work_ref"]["candidate_revision"] = "ab" * 20
            row["payload"]["summary"] = "alpha"
            lexical.append(row)
        exact = json.loads(json.dumps(base))
        exact["id"] = "f" * 32
        exact["work_ref"]["repository"] = "https://github.com/vu1n/kypp"
        exact["work_ref"]["candidate_revision"] = "cd" * 20
        exact["payload"]["summary"] = "alpha " + ("padding " * 80)
        ranked = _search([exact, *lexical], query="alpha", repository=exact["work_ref"]["repository"], candidate_revision=exact["work_ref"]["candidate_revision"])
        self.assertEqual(ranked["fts_rows"], 101)
        self.assertEqual(ranked["exact_rows"], 1)
        self.assertEqual(len(ranked["ids"]), 100)
        self.assertEqual(ranked["ids"][0], exact["id"])
        self.assertIs(ranked["window_capped"], True)
        self.assertNotIn(lexical[149]["id"], ranked["ids"])
        revision_only = []
        for row in lexical:
            copy = json.loads(json.dumps(row))
            copy["payload"]["summary"] = "quiet"
            copy["work_ref"]["candidate_revision"] = "ef" * 20
            revision_only.append(copy)
        exact_only = _search(revision_only, query="zzznomatch", repository=None, candidate_revision="ef" * 20)
        self.assertEqual(exact_only["fts_rows"], 0)
        self.assertEqual(exact_only["exact_rows"], 101)
        self.assertEqual(exact_only["ids"], sorted(row["id"] for row in revision_only)[:100])
        self.assertIs(exact_only["window_capped"], True)

    def test_exact_work_ref_matches_whichever_field_was_supplied(self):
        base = json.loads(json.dumps(FIXTURE["selection"]["records"][3]["record"]))
        shared = "ab" * 20
        left = json.loads(json.dumps(base))
        right = json.loads(json.dumps(base))
        other = json.loads(json.dumps(base))
        left["id"] = "a" * 32
        right["id"] = "b" * 32
        other["id"] = "c" * 32
        left["work_ref"] = {**left["work_ref"], "repository": "https://github.com/vu1n/left", "candidate_revision": shared}
        right["work_ref"] = {**right["work_ref"], "repository": "https://github.com/vu1n/right", "candidate_revision": shared}
        other["work_ref"] = {**other["work_ref"], "repository": "https://github.com/vu1n/left", "candidate_revision": "cd" * 20}
        for row in (left, right, other):
            row["payload"]["summary"] = "quiet"
        other["payload"]["summary"] = "alpha alpha alpha"
        by_revision = _search([other, right, left], query="alpha", repository=None, candidate_revision=shared, window=2)
        self.assertEqual(by_revision["ids"], [left["id"], right["id"]])
        self.assertIs(by_revision["window_capped"], True)
        by_repository = _search([other, right, left], query="zzznomatch", repository="https://github.com/vu1n/left", candidate_revision=None)
        self.assertEqual(by_repository["ids"], [left["id"], other["id"]])
        browsed = _search([other, right, left], query="", repository=None, candidate_revision=shared)
        self.assertEqual(browsed["ids"], [left["id"], right["id"], other["id"]])
        self.assertEqual(browsed["browse_rows"], 3)

    def test_bm25_ties_break_on_id(self):
        left = json.loads(json.dumps(FIXTURE["selection"]["records"][3]["record"]))
        right = json.loads(json.dumps(left))
        left["id"] = "a123456789abcdef0123456789abcdef"
        right["id"] = "b123456789abcdef0123456789abcdef"
        left["payload"]["summary"] = "sameword"
        right["payload"]["summary"] = "sameword"
        ranked = _search([right, left], query="SAMEWORD")
        self.assertEqual(ranked["ids"], [left["id"], right["id"]])

    def test_json_schema_accepts_fixture_inputs(self):
        append = Draft7Validator(APPEND_CONTEXT_INPUT_SCHEMA)
        query = Draft7Validator(QUERY_CONTEXT_INPUT_SCHEMA)
        for item in FIXTURE["cases"]:
            validator = append if item["op"] == "append" else query
            ok = validator.is_valid(item["input"])
            if item["expect"] == "accept":
                self.assertTrue(ok, item["id"] + " " + "; ".join(error.message for error in validator.iter_errors(item["input"])))
            else:
                with self.assertRaises(CooperationError):
                    _run(item)
        nested = json.loads(json.dumps(BY_ID["empty-handoff"]["input"]))
        nested["work_ref"]["note"] = "extra"
        self.assertFalse(append.is_valid(nested))
        payload = json.loads(json.dumps(BY_ID["empty-handoff"]["input"]))
        payload["payload"]["authority"] = "human"
        self.assertFalse(append.is_valid(payload))
        self.assertFalse(query.is_valid({"schema_version": 1, "project": "kypp", "work_ref": {"repository": "https://github.com/vu1n/kypp", "note": "x"}}))

    def test_stamped_records_reject_bad_id_and_kind(self):
        stamp = FIXTURE["visibility"]["stamp"]
        record = stamp_record({**stamp, "input": validate_append_context(BY_ID[stamp["case"]]["input"])})
        record["client_label"] = None
        record["session_id"] = None
        draft = {
            "schema_version": 1,
            "as_of": record["created_at"],
            "cutoff": record["created_at"],
            "selector_version": "cooperation-fts5-bm25-v1",
            "records": [record],
            "selected": [{"id": record["id"], "revision": record_revision(record), "fresh": is_fresh(record["expires_at"], record["created_at"])}],
            "max_bytes": 32768,
            "omissions": [],
            "diagnostics": [],
            "window_capped": False,
            "candidate_window_exhaustive": True,
        }
        sealed = seal_contribution(draft)
        self.assertEqual(validate_contribution(sealed)["records"][0]["id"], record["id"])
        sparse = json.loads(json.dumps(sealed))
        for key in ("client_label", "session_id", "reply_to", "supersedes"):
            del sparse["records"][0][key]
        parsed = validate_contribution(sparse)
        self.assertIsNone(parsed["records"][0]["client_label"])
        self.assertIsNone(parsed["records"][0]["session_id"])
        self.assertIsNone(parsed["records"][0]["reply_to"])
        self.assertIsNone(parsed["records"][0]["supersedes"])
        self.assertIn("client_label", parsed["records"][0])
        self.assertEqual(parsed["records"], sealed["records"])
        self.assertEqual(parsed["budget_bytes"], utf8_bytes(canonical_json(parsed)))
        bad_id = json.loads(json.dumps(sealed))
        bad_id["records"][0]["id"] = "NOT-AN-ID"
        with self.assertRaises(CooperationError) as caught:
            validate_contribution(bad_id)
        self.assertEqual(caught.exception.code, "INVALID_RECORD")
        self.assertIn("id", str(caught.exception))
        bad_kind = json.loads(json.dumps(sealed))
        bad_kind["records"][0]["kind"] = "root"
        with self.assertRaises(CooperationError) as caught:
            validate_contribution(bad_kind)
        self.assertEqual(caught.exception.code, "INVALID_RECORD")
        self.assertIn("kind", str(caught.exception))

    def test_nullable_lineage_links_match_schema_and_retry_digest(self):
        validator = Draft7Validator(APPEND_CONTEXT_INPUT_SCHEMA)
        base = BY_ID["empty-handoff"]["input"]
        expected = client_payload_digest(validate_append_context(base))
        for links in ({"reply_to": None}, {"supersedes": None}, {"reply_to": None, "supersedes": None}):
            with self.subTest(links=links):
                request = {**base, **links}
                self.assertTrue(validator.is_valid(request))
                self.assertEqual(client_payload_digest(validate_append_context(request)), expected)

    def test_conflict_diagnostics_validate_shared_cases(self):
        diagnostic = DIAGNOSTICS["valid"]
        draft = {**FIXTURE["contribution"]["draft"], "diagnostics": [diagnostic]}
        self.assertEqual(validate_contribution(seal_contribution(draft))["diagnostics"], [diagnostic])
        with_run = {**diagnostic, "causal_key": {**diagnostic["causal_key"], "original_run_id": "run-1"}}
        self.assertEqual(validate_contribution(seal_contribution({**draft, "diagnostics": [with_run]}))["diagnostics"], [with_run])
        # More citations than variants is valid: arrays are independent sets, not paired entries.
        more_citations = {**diagnostic, "record_ids": [*diagnostic["record_ids"], "2" * 32]}
        validate_contribution(seal_contribution({**draft, "diagnostics": [more_citations]}))
        at_cap = {**diagnostic,
                  "record_ids": [f"{i:032x}" for i in range(16)],
                  "fingerprints": [f"{i:064x}" for i in range(16)]}
        validate_contribution(seal_contribution({**draft, "diagnostics": [at_cap]}))
        validate_contribution(seal_contribution({**draft, "diagnostics": [diagnostic] * 16}))
        for bad in ({}, None, {**diagnostic, "code": "verified"}):
            with self.assertRaises(CooperationError):
                validate_contribution(seal_contribution({**draft, "diagnostics": [bad]}))
        with self.assertRaises(CooperationError):
            validate_contribution(seal_contribution({**draft, "diagnostics": [diagnostic] * 17}))
        for case in DIAGNOSTICS["invalid"]:
            with self.subTest(case["id"]):
                bad = json.loads(json.dumps(diagnostic))
                target = bad
                for key in case["path"][:-1]:
                    target = target[key]
                if case.get("omit"):
                    del target[case["path"][-1]]
                else:
                    target[case["path"][-1]] = case["value"]
                # Reseal so rejection exercises the payload, not a stale byte budget.
                sealed = seal_contribution({**draft, "diagnostics": [bad]})
                with self.assertRaises(CooperationError) as caught:
                    validate_contribution(sealed)
                self.assertEqual(caught.exception.code, "INVALID_RECORD")

    def test_contribution_budget(self):
        sealed = seal_contribution(FIXTURE["contribution"]["draft"])
        self.assertEqual(sha256_hex(canonical_json(sealed)), FIXTURE["contribution"]["sha256"])
        self.assertEqual(sealed["budget_bytes"], utf8_bytes(canonical_json(sealed)))
        self.assertEqual(validate_contribution(sealed)["budget_bytes"], sealed["budget_bytes"])

    def test_contribution_timestamps_reject_invalid_values(self):
        for field in ("as_of", "cutoff"):
            for case in [*REGRESSIONS["invalid_timestamp_values"], {"id": "missing"}]:
                with self.subTest(field=field, case=case["id"]):
                    draft = dict(FIXTURE["contribution"]["draft"])
                    if "value" in case:
                        draft[field] = case["value"]
                    else:
                        del draft[field]
                    # Reseal so rejection checks the timestamp, not a stale budget.
                    sealed = seal_contribution(draft)
                    with self.assertRaises(CooperationError) as caught:
                        validate_contribution(sealed)
                    self.assertEqual(caught.exception.code, "INVALID_RECORD")
                    self.assertIn(field, str(caught.exception))

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
