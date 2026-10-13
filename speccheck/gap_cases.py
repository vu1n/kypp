"""False-pass cases for the gaps closed after the Worker signature checker.

These cases were written with the rules, so they are not the frozen Worker corpus.
"""

from __future__ import annotations

import copy

from .bench_cases import Case
from .fixtures import MEMORY_TS, MCP_PY, clean_tree, ref, with_structural_specs


def _case(case_id: str, split: str, description: str, head: dict[str, str], **kwargs) -> Case:
    return Case(case_id, split, description, clean_tree(), head, **kwargs)


def _edit(mutate) -> dict[str, str]:
    tree = clean_tree()
    mutate(tree)
    return tree


def _spec(case_id: str, split: str, description: str, mutate, **kwargs) -> Case:
    baseline = with_structural_specs(clean_tree())
    head = copy.deepcopy(baseline)
    mutate(head)
    return Case(case_id, split, description, baseline, head, **kwargs)


def _ts(source: str) -> dict[str, str]:
    return _edit(lambda t: t.__setitem__("worker/src/memory.ts", source))


def gap_cases() -> list[Case]:
    stale = clean_tree()
    stale[".brief/docs/verify-operator-only/versions/v0001.md"] = stale[".brief/docs/verify-operator-only/versions/v0002.md"]
    stale["kypp/mcp_server.py"] += "\n# " + ref("kypp/verify-operator-only@0001#operator-only-verify") + "\n"
    stale["kypp/note.py"] = "# " + ref("kypp/verify-operator-only@0001#operator-only-verify") + "\n"
    return [
        _case(
            "tuning_add_tool_clean",
            "tuning",
            "mcp.add_tool of one local function with no verify parameter is covered",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py", MCP_PY + '\ndef ping() -> str:\n    return "ok"\nmcp.add_tool(ping)\n')),
            expect_status={"mcp-no-verify-argument": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "tuning_unpack_typeddict",
            "tuning",
            "Unpack[TypedDict] with no verify field is a closed parameter list",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py",
                "from typing import TypedDict, Unpack\nclass ClaimArgs(TypedDict):\n    subject: str\n    content: str\n"
                "def build_mcp(store):\n    @mcp.tool()\n    def claim(**kwargs: Unpack[ClaimArgs]) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n")),
            expect_status={"mcp-no-verify-argument": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _spec(
            "tuning_literal_delete",
            "tuning",
            "a string literal DELETE FROM memory_claims violates the append-only bullet",
            lambda t: t.__setitem__("kypp/wipe.py", 'def wipe(db):\n    db.execute("DELETE FROM memory_claims")\n'),
            expect_status={"no-delete-claims": "violates"},
            expect_violation_ids={"no-delete-claims"},
            expect_severity={"no-delete-claims": "high"},
        ),
        _spec(
            "tuning_grader_counts",
            "tuning",
            "ClaimDraft text built from counts and grader_scheme conforms",
            lambda t: t.__setitem__("kypp/distill.py",
                "def draft(trace):\n    n = len(trace.verdict.failed_criteria)\n    scheme = grader_scheme(trace.verdict.grader)\n"
                "    ClaimDraft(\"pitfall\", f\"{scheme} failed ({n})\", f\"{n} of {scheme}\")\n"),
            expect_status={"no-grader-verbatim": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "tuning_mark_verified_sql",
            "tuning",
            "authority=verified SQL inside MemoryStore.mark_verified is the allowed writer",
            _edit(lambda t: t.__setitem__("kypp/extra.py",
                "class MemoryStore:\n    def mark_verified(self, db):\n"
                "        db.execute(\"UPDATE memory_claims SET authority='verified' WHERE id=1\")\n")),
            expect_status={"verified-authority-writer": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_add_tool_unknown",
            "gap-holdout",
            "mcp.add_tool of a name that is not a local function is not conformance",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py", MCP_PY + "\nmcp.add_tool(missing)\n")),
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_add_tool_verify",
            "gap-holdout",
            "an undecorated function registered with mcp.add_tool can still accept verify",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py", MCP_PY + "\ndef hidden(verify: str) -> str:\n    return verify\nmcp.add_tool(hidden)\n")),
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
            expect_severity={"mcp-no-verify-argument": "high"},
        ),
        _case(
            "holdout_unpack_verify_field",
            "gap-holdout",
            "Unpack[TypedDict] expands a verify field even though the parameter is named kwargs",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py",
                "from typing import TypedDict, Unpack\nclass ClaimArgs(TypedDict):\n    subject: str\n    verify: str\n"
                "def build_mcp(store):\n    @mcp.tool()\n    def claim(**kwargs: Unpack[ClaimArgs]) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n")),
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
            expect_severity={"mcp-no-verify-argument": "high"},
        ),
        _case(
            "holdout_model_verify_field",
            "gap-holdout",
            "a pydantic parameter model with a verify field is a verify argument",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py",
                "class ClaimArgs(BaseModel):\n    subject: str\n    verify: str | None = None\n"
                "def build_mcp(store):\n    @mcp.tool()\n    def claim(args: ClaimArgs) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n")),
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
            expect_severity={"mcp-no-verify-argument": "high"},
        ),
        _case(
            "holdout_model_extra_enum",
            "gap-holdout",
            "class Config: extra = Extra.allow is not a proved literal forbid or ignore",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py",
                "class ClaimArgs(BaseModel):\n    class Config:\n        extra = Extra.allow\n    subject: str\n"
                "def build_mcp(store):\n    @mcp.tool()\n    def claim(args: ClaimArgs) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n")),
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_model_extra_allow",
            "gap-holdout",
            "a parameter model with extra=allow is not a closed field list",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py",
                "class ClaimArgs(BaseModel):\n    model_config = {\"extra\": \"allow\"}\n    subject: str\n"
                "def build_mcp(store):\n    @mcp.tool()\n    def claim(args: ClaimArgs) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n")),
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_imported_param_model",
            "gap-holdout",
            "a parameter model imported from outside the tree is not proved free of verify",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py",
                "from other.models import ClaimArgs\ndef build_mcp(store):\n    @mcp.tool()\n"
                "    def claim(args: ClaimArgs) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n")),
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_starargs",
            "gap-holdout",
            "a decorated tool that accepts *args is not a proved parameter list",
            _edit(lambda t: t.__setitem__("kypp/mcp_server.py", MCP_PY.replace(
                "def claim(subject: str, content: str, code_refs: list | None = None) -> str:",
                "def claim(subject: str, *args) -> str:", 1))),
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case("holdout_ts_authorities_reversed", "gap-holdout", "worker/src/memory.ts AUTHORITIES reversed against the Python literal",
              _ts(MEMORY_TS.replace('["agent", "verified", "human"]', '["human", "verified", "agent"]', 1)),
              expect_status={"authority-rank-order": "violates"}, expect_violation_ids={"authority-rank-order"},
              expect_severity={"authority-rank-order": "medium"}),
        _case("holdout_ts_negated_index", "gap-holdout", "negating AUTHORITIES.indexOf flips the TypeScript rank",
              _ts(MEMORY_TS.replace("AUTHORITIES.indexOf(c.authority)", "-AUTHORITIES.indexOf(c.authority)", 1)),
              expect_status={"authority-rank-order": "violates"}, expect_violation_ids={"authority-rank-order"},
              expect_severity={"authority-rank-order": "medium"}),
        _case("holdout_ts_reverse_indexof", "gap-holdout", "AUTHORITIES.reverse().indexOf is a flipped rank even though indexOf is present",
              _ts(MEMORY_TS.replace("AUTHORITIES.indexOf(c.authority)", "AUTHORITIES.reverse().indexOf(c.authority)", 1)),
              expect_status={"authority-rank-order": "violates"}, expect_violation_ids={"authority-rank-order"},
              expect_severity={"authority-rank-order": "medium"}),
        _case("holdout_ts_unproved_rank", "gap-holdout", "an unrecognised TypeScript rank expression is not conformance",
              _ts(MEMORY_TS.replace("AUTHORITIES.indexOf(c.authority)", "blend(AUTHORITIES, c.authority)", 1)),
              expect_status={"authority-rank-order": "unresolved"}, expect_violation_ids=set()),
        _case("holdout_ts_memory_missing", "gap-holdout", "a clean Python rank with worker/src/memory.ts absent is not conformance",
              _edit(lambda t: t.pop("worker/src/memory.ts")),
              expect_status={"authority-rank-order": "unresolved"}, expect_violation_ids=set()),
        _case("holdout_ts_rank_not_bound", "gap-holdout", "an incidental rank() call does not prove the compared names are rank(a) and rank(b)",
              _ts(MEMORY_TS.replace(
                  "const ra = rank(a), rb = rank(b);",
                  "rank(a);\n  const ra = [a.confidence], rb = [b.confidence];",
              )),
              expect_status={"authority-rank-order": "unresolved"}, expect_violation_ids=set()),
        _case("holdout_ts_survivor_keeps_lower", "gap-holdout", "a survivor that keeps the lower compareRank flips the TypeScript order",
              _ts(MEMORY_TS.replace("compareRank(c, best) > 0 ? c : best", "compareRank(c, best) < 0 ? c : best", 1)),
              expect_status={"authority-rank-order": "violates"}, expect_violation_ids={"authority-rank-order"},
              expect_severity={"authority-rank-order": "medium"}),
        _spec("holdout_delete_concat", "gap-holdout", "DELETE FROM split across two string literals is still one statement",
              lambda t: t.__setitem__("kypp/wipe.py", 'def wipe(db):\n    db.execute("DELETE FROM " + "memory_claims")\n'),
              expect_status={"no-delete-claims": "violates"}, expect_violation_ids={"no-delete-claims"},
              expect_severity={"no-delete-claims": "high"}),
        _spec("holdout_delete_bound_name", "gap-holdout", "a local bound to the table name still completes DELETE FROM",
              lambda t: t.__setitem__("kypp/wipe.py", 'def wipe(db):\n    table = "memory_claims"\n    db.execute("DELETE FROM " + table)\n'),
              expect_status={"no-delete-claims": "violates"}, expect_violation_ids={"no-delete-claims"},
              expect_severity={"no-delete-claims": "high"}),
        _spec("holdout_delete_join", "gap-holdout", "''.join of the DELETE pieces is one constant statement",
              lambda t: t.__setitem__("kypp/wipe.py", 'def wipe(db):\n    db.execute("".join(["DELETE FROM ", "memory_claims"]))\n'),
              expect_status={"no-delete-claims": "violates"}, expect_violation_ids={"no-delete-claims"},
              expect_severity={"no-delete-claims": "high"}),
        _spec("holdout_delete_fstring", "gap-holdout", "DELETE FROM an f-string hole is not a proved table",
              lambda t: t.__setitem__("kypp/wipe.py", 'def wipe(db, table):\n    db.execute(f"DELETE FROM {table}")\n'),
              expect_status={"no-delete-claims": "candidate"}, expect_violation_ids=set()),
        _spec("holdout_delete_docstring", "gap-holdout", "DELETE text that exists only in docstrings is not a SQL statement",
              lambda t: t.__setitem__("kypp/wipe.py", '"""DELETE FROM memory_claims"""\ndef wipe(db):\n    """DELETE FROM observations"""\n    return db\n'),
              expect_status={"no-delete-claims": "no_counterexample"}, expect_violation_ids=set()),
        _spec("holdout_delete_bracket_table", "gap-holdout",
              "DELETE FROM [memory_claims] is still a delete of the protected table",
              lambda t: t.__setitem__("kypp/wipe.py", 'def wipe(db):\n    db.execute("DELETE FROM [memory_claims]")\n'),
              expect_status={"no-delete-claims": "violates"}, expect_violation_ids={"no-delete-claims"},
              expect_severity={"no-delete-claims": "high"}),
        _spec("holdout_delete_worker_ts", "gap-holdout", "a worker TypeScript string can delete memory_claims",
              lambda t: t.__setitem__("worker/src/extra.ts", 'db.exec("DELETE FROM memory_claims")\n'),
              expect_status={"no-delete-claims": "violates"}, expect_violation_ids={"no-delete-claims"},
              expect_severity={"no-delete-claims": "high"}),
        _spec("holdout_delete_worker_template", "gap-holdout", "a worker template DELETE whose table is interpolated is not conformance",
              lambda t: t.__setitem__("worker/src/extra.ts", "db.exec(`DELETE FROM ${table}`)\n"),
              expect_status={"no-delete-claims": "candidate"}, expect_violation_ids=set()),
        _case("holdout_verified_claim", "gap-holdout", "store.claim(..., authority='verified') outside mark_verified is a writer",
              _edit(lambda t: t.__setitem__("kypp/extra.py", 'def promote(store):\n    store.claim("fact", "s", "c", authority="verified")\n')),
              expect_status={"verified-authority-writer": "violates"},
              expect_violation_ids={"verified-authority-writer"},
              expect_severity={"verified-authority-writer": "high"}),
        _case("holdout_verified_folded_name", "gap-holdout", "a local bound to the string verified is still authority=verified",
              _edit(lambda t: t.__setitem__("kypp/extra.py", 'def promote(store):\n    level = "verified"\n    store.claim("fact", "s", "c", authority=level)\n')),
              expect_status={"verified-authority-writer": "violates"},
              expect_violation_ids={"verified-authority-writer"},
              expect_severity={"verified-authority-writer": "high"}),
        _case("holdout_verified_param", "gap-holdout", "a non-constant authority argument is not proved to be other than verified",
              _edit(lambda t: t.__setitem__("kypp/extra.py", 'def promote(store, level):\n    store.claim("fact", "s", "c", authority=level)\n')),
              expect_status={"verified-authority-writer": "unresolved"}, expect_violation_ids=set()),
        _case("holdout_verified_sql", "gap-holdout", "UPDATE authority='verified' outside MemoryStore.mark_verified is a writer",
              _edit(lambda t: t.__setitem__("kypp/extra.py", 'def promote(db):\n    db.execute("UPDATE memory_claims SET authority=\'verified\' WHERE id=1")\n')),
              expect_status={"verified-authority-writer": "violates"},
              expect_violation_ids={"verified-authority-writer"},
              expect_severity={"verified-authority-writer": "high"}),
        _case("holdout_verified_insert_values", "gap-holdout",
              "INSERT column/value correspondence that places verified in authority is a writer",
              _edit(lambda t: t.__setitem__("kypp/extra.py",
                  "def promote(db):\n    db.execute(\"INSERT INTO memory_claims(subject, authority) VALUES ('s', 'verified')\")\n")),
              expect_status={"verified-authority-writer": "violates"},
              expect_violation_ids={"verified-authority-writer"},
              expect_severity={"verified-authority-writer": "high"}),
        _case("holdout_verified_worker_sql", "gap-holdout", "a worker string that sets authority=verified is a writer",
              _edit(lambda t: t.__setitem__("worker/src/extra.ts", 'db.exec("UPDATE memory_claims SET authority=\'verified\'")\n')),
              expect_status={"verified-authority-writer": "violates"},
              expect_violation_ids={"verified-authority-writer"},
              expect_severity={"verified-authority-writer": "high"}),
        _spec("holdout_grader_subscript", "gap-holdout", "ClaimDraft subject and content that subscript name and feedback leak the grader",
              lambda t: t.__setitem__("kypp/distill.py", 'def draft(c):\n    return ClaimDraft("fact", c["name"], c["feedback"])\n'),
              expect_status={"no-grader-verbatim": "violates"}, expect_violation_ids={"no-grader-verbatim"},
              expect_severity={"no-grader-verbatim": "medium"}),
        _spec("holdout_grader_alias", "gap-holdout", "a local copied from a criterion name still leaks when it reaches ClaimDraft",
              lambda t: t.__setitem__("kypp/distill.py", 'def draft(crit):\n    label = crit["name"]\n    return ClaimDraft("fact", label, "ok")\n'),
              expect_status={"no-grader-verbatim": "violates"}, expect_violation_ids={"no-grader-verbatim"},
              expect_severity={"no-grader-verbatim": "medium"}),
        _spec("holdout_grader_feedback_attr", "gap-holdout", "attribute .feedback on a criterion is grader text",
              lambda t: t.__setitem__("kypp/distill.py", 'def draft(crit):\n    return ClaimDraft("fact", crit.feedback, "x")\n'),
              expect_status={"no-grader-verbatim": "violates"}, expect_violation_ids={"no-grader-verbatim"},
              expect_severity={"no-grader-verbatim": "medium"}),
        _spec("holdout_grader_attr_name", "gap-holdout", "attribute .name might be a tool name, so it is not proved clean",
              lambda t: t.__setitem__("kypp/distill.py", 'def draft(crit):\n    return ClaimDraft("fact", crit.name, "x")\n'),
              expect_status={"no-grader-verbatim": "unresolved"}, expect_violation_ids=set()),
        _spec("holdout_grader_mixed_unknown", "gap-holdout", "one clean ClaimDraft does not prove a later draft copied from model JSON",
              lambda t: t.__setitem__("kypp/distill.py",
                  'def draft(rows, it):\n    ClaimDraft("fact", "n", str(len(rows.criteria)))\n'
                  '    content = it.get("content")\n    ClaimDraft("fact", "copied", content)\n'),
              expect_status={"no-grader-verbatim": "unresolved"}, expect_violation_ids=set()),
        _case(
            "holdout_verified_upsert",
            "gap-holdout",
            "ON CONFLICT DO UPDATE SET authority='verified' is a writer even after a non-verified VALUES row",
            _edit(lambda t: t.__setitem__(
                "kypp/extra.py",
                "def promote(db):\n"
                "    db.execute(\"INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') "
                "ON CONFLICT(subject) DO UPDATE SET authority='verified'\")\n",
            )),
            expect_status={"verified-authority-writer": "violates"},
            expect_violation_ids={"verified-authority-writer"},
            expect_severity={"verified-authority-writer": "high"},
        ),
        _case(
            "holdout_verified_upsert_agent",
            "gap-holdout",
            "an upsert that sets authority='agent' is not the allow-listed INSERT, so it does not conform",
            _edit(lambda t: t.__setitem__(
                "kypp/extra.py",
                "def promote(db):\n"
                "    db.execute(\"INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') "
                "ON CONFLICT(subject) DO UPDATE SET authority='agent'\")\n",
            )),
            expect_status={"verified-authority-writer": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_verified_returning",
            "gap-holdout",
            "INSERT ... RETURNING is outside the allow-list even when the mapped value is not verified",
            _edit(lambda t: t.__setitem__(
                "kypp/extra.py",
                "def promote(db):\n"
                "    db.execute(\"INSERT INTO memory_claims(subject, authority) VALUES ('s', 'agent') RETURNING id\")\n",
            )),
            expect_status={"verified-authority-writer": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_verified_replace",
            "gap-holdout",
            "REPLACE INTO that places verified in the authority column is a writer",
            _edit(lambda t: t.__setitem__(
                "kypp/extra.py",
                "def promote(db):\n"
                "    db.execute(\"REPLACE INTO memory_claims(authority) VALUES ('verified')\")\n",
            )),
            expect_status={"verified-authority-writer": "violates"},
            expect_violation_ids={"verified-authority-writer"},
            expect_severity={"verified-authority-writer": "high"},
        ),
        _case(
            "holdout_ts_rank_early_return",
            "gap-holdout",
            "compareRank does not conform when an earlier return ignores the rank bindings",
            _ts(MEMORY_TS.replace(
                "  const ra = rank(a), rb = rank(b);\n  for (let i = 0; i < ra.length; i++) {",
                "  const ra = rank(a), rb = rank(b);\n  return a.confidence - b.confidence;\n"
                "  for (let i = 0; i < ra.length; i++) {",
                1,
            )),
            expect_status={"authority-rank-order": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_ts_rank_extra_if",
            "gap-holdout",
            "an extra return inside the compareRank loop is not the one proved shape",
            _ts(MEMORY_TS.replace(
                "    if (ra[i] > rb[i]) return 1;\n",
                "    if (ra[i] > rb[i]) return 1;\n    if (ra[i] == rb[i]) return 0;\n",
                1,
            )),
            expect_status={"authority-rank-order": "unresolved"},
            expect_violation_ids=set(),
        ),
        _spec(
            "holdout_delete_ts_concat",
            "gap-holdout",
            "worker SQL split across + leaves is still one DELETE FROM memory_claims",
            lambda t: t.__setitem__("worker/src/extra.ts", 'db.exec("DELETE FROM " + "memory_claims")\n'),
            expect_status={"no-delete-claims": "violates"},
            expect_violation_ids={"no-delete-claims"},
            expect_severity={"no-delete-claims": "high"},
        ),
        _spec(
            "holdout_delete_trigger",
            "gap-holdout",
            "DELETE FROM memory_claims inside a trigger body is still a delete",
            lambda t: t.__setitem__(
                "kypp/wipe.py",
                'def wipe(db):\n    db.executescript("CREATE TRIGGER t AFTER INSERT ON memory_claims '
                'BEGIN DELETE FROM memory_claims; END")\n',
            ),
            expect_status={"no-delete-claims": "violates"},
            expect_violation_ids={"no-delete-claims"},
            expect_severity={"no-delete-claims": "high"},
        ),
        _spec(
            "holdout_delete_cte",
            "gap-holdout",
            "a CTE whose statement deletes observations is still a delete",
            lambda t: t.__setitem__(
                "kypp/wipe.py",
                'def wipe(db):\n    db.execute("WITH gone AS (SELECT 1) DELETE FROM observations")\n',
            ),
            expect_status={"no-delete-claims": "violates"},
            expect_violation_ids={"no-delete-claims"},
            expect_severity={"no-delete-claims": "high"},
        ),
        _spec(
            "holdout_delete_cte_read",
            "gap-holdout",
            "a CTE that only reads memory_claims is not a DELETE-like construction",
            lambda t: t.__setitem__(
                "kypp/wipe.py",
                'def wipe(db):\n    db.execute("WITH c AS (SELECT id FROM memory_claims) SELECT id FROM c")\n',
            ),
            expect_status={"no-delete-claims": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_schema_trailing_drop",
            "gap-holdout",
            "ADD COLUMN followed by a second statement is not an additive migration",
            _edit(lambda t: t.__setitem__(
                "kypp/store.py",
                t["kypp/store.py"].replace(
                    '"ALTER TABLE memory_claims ADD COLUMN user TEXT"',
                    '"ALTER TABLE memory_claims ADD COLUMN user TEXT; DROP TABLE observations"',
                    1,
                ),
            )),
            expect_status={"schema-add-column": "violates"},
            expect_violation_ids={"schema-add-column"},
            expect_severity={"schema-add-column": "medium"},
        ),
        _case(
            "holdout_schema_check",
            "gap-holdout",
            "ADD COLUMN with a CHECK clause is not the allow-listed migration shape",
            _edit(lambda t: t.__setitem__(
                "kypp/store.py",
                t["kypp/store.py"].replace(
                    '"ALTER TABLE memory_claims ADD COLUMN user TEXT"',
                    '"ALTER TABLE memory_claims ADD COLUMN user TEXT CHECK (length(user) > 0)"',
                    1,
                ),
            )),
            expect_status={"schema-add-column": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_model_config_spread",
            "gap-holdout",
            "a model_config dict spread is not a proved literal extra policy, even if extra=forbid is visible",
            _edit(lambda t: t.__setitem__(
                "kypp/mcp_server.py",
                "class ClaimArgs(BaseModel):\n    model_config = {**base, \"extra\": \"forbid\"}\n    subject: str\n"
                "def build_mcp(store):\n    @mcp.tool()\n    def claim(args: ClaimArgs) -> str:\n        return \"ok\"\n"
                "    @mcp.tool()\n    def correct(subject: str, content: str) -> str:\n        return \"ok\"\n",
            )),
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _spec(
            "holdout_grader_wrapped_leak",
            "gap-holdout",
            "wrapping a criterion name in an unknown call does not prove the draft is clean",
            lambda t: t.__setitem__(
                "kypp/distill.py",
                'def draft(crit):\n    return ClaimDraft("fact", obscure(crit["name"]), "x")\n',
            ),
            expect_status={"no-grader-verbatim": "unresolved"},
            expect_violation_ids=set(),
        ),
        Case(
            "holdout_stale_pin_deduped",
            "gap-holdout",
            "two occurrences of one stale pin are a single advisory that lists both",
            stale,
            copy.deepcopy(stale),
            expect_status={"doc-refs": "bounded"},
            expect_violation_ids=set(),
            expect_advisory="stale-pin",
            expect_advisory_count=1,
            expect_advisory_contains=("kypp/mcp_server.py:", "kypp/note.py:1"),
        ),
    ]
