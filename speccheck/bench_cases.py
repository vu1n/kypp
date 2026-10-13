"""Tuning, exposed regression, independent holdout, and smoke cases.

Legacy holdout IDs are preserved as provenance, but their split is regression.
Smoke controls are not part of the false-conformance rate.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .fixtures import clean_tree, ref, with_pr22


@dataclass
class Case:
    id: str
    split: str
    description: str
    baseline: dict[str, str]
    head: dict[str, str]
    expect_status: dict[str, str] = field(default_factory=dict)
    expect_violation_ids: set[str] | None = None
    expect_severity: dict[str, str] = field(default_factory=dict)
    expect_amendments: bool | None = None
    expect_advisory: str | None = None
    expect_advisory_count: int | None = None
    expect_advisory_contains: tuple[str, ...] = ()
    expect_not_clean_single_writer: bool = False


def _case(case_id: str, split: str, description: str, head: dict[str, str], **kwargs) -> Case:
    return Case(
        id=case_id,
        split=split,
        description=description,
        baseline=clean_tree(),
        head=head,
        **kwargs,
    )


def _edit(mutate) -> dict[str, str]:
    tree = clean_tree()
    mutate(tree)
    return tree


def tuning_cases() -> list[Case]:
    base = clean_tree()
    pr22 = with_pr22(base)
    amended = with_pr22(base)
    amended[".brief/docs/verify-operator-only/versions/v0002.md"] = amended[
        ".brief/docs/verify-operator-only/versions/v0002.md"
    ].replace(
        "MCP tools never accept a `verify` argument.",
        "MCP tools may accept a `verify` argument.",
        1,
    )
    kwargs_tree = _edit(
        lambda t: t.__setitem__(
            "kypp/mcp_server.py",
            t["kypp/mcp_server.py"].replace(
                "def claim(subject: str, content: str, code_refs: list | None = None) -> str:",
                "def claim(subject: str, content: str, **kwargs) -> str:",
                1,
            ),
        )
    )
    partial = with_pr22(base)
    del partial["worker/src/mcp.ts"]
    syntax = _edit(lambda t: t.__setitem__("kypp/mcp_server.py", "def build(\n"))
    not_null = _edit(
        lambda t: t.__setitem__(
            "kypp/store.py",
            '_MIGRATIONS = [\n    "ALTER TABLE memory_claims ADD COLUMN x TEXT NOT NULL",\n]\n',
        )
    )
    human = _edit(
        lambda t: t.__setitem__(
            "kypp/mcp_server.py",
            t["kypp/mcp_server.py"].replace(
                'return store.claim("fact", subject, content, accept=True)',
                'return store.claim("fact", subject, content, accept=True, authority="human")',
                1,
            ),
        )
    )
    swapped = _edit(
        lambda t: t.__setitem__(
            "kypp/mcp_server.py",
            t["kypp/mcp_server.py"] + "\n# " + ref("kypp/operator-only-verify@0002#verify-operator-only") + "\n",
        )
    )
    rank = _edit(
        lambda t: t.__setitem__(
            "kypp/vocab.py",
            t["kypp/vocab.py"].replace('Literal["agent", "verified", "human"]', 'Literal["human", "agent", "verified"]', 1),
        )
    )
    prose = _edit(
        lambda t: t.__setitem__(
            ".brief/docs/example-open.md",
            "---\nid: example-open\ntype: decision\nstatus: active\n---\n\n"
            "<!-- brief:anchor example-open -->\n### Invariant\n"
            "- When the tests pass, the moon is made of cheese.\n",
        )
    )
    abstain = clean_tree()
    del abstain["worker/src/mcp.ts"]
    return [
        _case(
            "pr22_verify_arg",
            "tuning",
            "PR #22: MCP claim gains verify, and SIGNOFF claims it did not",
            pr22,
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
            expect_severity={"mcp-no-verify-argument": "high"},
        ),
        _case(
            "post_sync_not_single_writer",
            "tuning",
            "authority-order keeps the POST /sync bullet with the correct_main bullet",
            clean_tree(),
            expect_status={"human-authority-writers": "unresolved"},
            expect_violation_ids=set(),
            expect_not_clean_single_writer=True,
        ),
        _case(
            "amended_spec_still_violates",
            "tuning",
            "head spec allows verify and the code adds it; baseline still forbids it",
            amended,
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
            expect_severity={"mcp-no-verify-argument": "high"},
            expect_amendments=True,
        ),
        _case(
            "timeout_behaviour_abstains",
            "tuning",
            "the timeout bullet is behaviour; a missing test is not conformance",
            clean_tree(),
            expect_status={"verify-timeout-behaviour": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "kwargs_not_covered",
            "tuning",
            "a decorated tool with **kwargs is not a closed parameter list",
            kwargs_tree,
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "counterexample_with_partial_scope",
            "tuning",
            "worker MCP file is missing, and the Python tool still accepts verify",
            partial,
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
        ),
        _case(
            "incomplete_scope_abstains",
            "tuning",
            "worker MCP file is missing and no counterexample was found",
            abstain,
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        _case(
            "schema_not_null",
            "tuning",
            "ADD COLUMN NOT NULL without a default",
            not_null,
            expect_status={"schema-add-column": "violates"},
            expect_violation_ids={"schema-add-column"},
            expect_severity={"schema-add-column": "medium"},
        ),
        _case(
            "mcp_authority_human",
            "tuning",
            "MCP correct passes authority=human",
            human,
            expect_status={"mcp-no-human-or-verified-authority": "violates"},
            expect_violation_ids={"mcp-no-human-or-verified-authority"},
            expect_severity={"mcp-no-human-or-verified-authority": "high"},
        ),
        _case(
            "id_anchor_swap",
            "tuning",
            "document id and anchor are swapped in a doc:// ref",
            swapped,
            expect_status={"doc-refs": "violates"},
            expect_violation_ids={"doc-refs"},
            expect_severity={"doc-refs": "low"},
        ),
        _case(
            "rank_order_swapped",
            "tuning",
            "Authority literal is not agent < verified < human",
            rank,
            expect_status={"authority-rank-order": "violates"},
            expect_violation_ids={"authority-rank-order"},
            expect_severity={"authority-rank-order": "medium"},
        ),
        _case(
            "unparsed_scope_abstains",
            "tuning",
            "the MCP file does not parse and there is no counterexample",
            syntax,
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
        Case(
            id="prose_unsupported",
            split="tuning",
            description="a baseline invariant with no deterministic rule stays unsupported",
            baseline=prose,
            head=copy.deepcopy(prose),
            expect_status={"unsupported:example-open@live#example-open#0": "unsupported"},
            expect_violation_ids=set(),
        ),
    ]


def regression_cases() -> list[Case]:
    worker_verify = _edit(
        lambda t: t.__setitem__(
            "worker/src/mcp.ts",
            t["worker/src/mcp.ts"].replace(
                'subject: { type: "string" },\n        content: { type: "string" },',
                'subject: { type: "string" },\n        content: { type: "string" },\n        verify: { type: "string" },',
                1,
            ),
        )
    )
    # The bad alias is the baseline spec itself. A head-only alias edit would be an amendment, not a rule.
    bad_alias_base = clean_tree()
    bad_alias_base[".brief/aliases.yaml"] = bad_alias_base[".brief/aliases.yaml"].replace(
        "verify-operator-only:\n  latest: 2\n", "verify-operator-only:\n  latest: 9\n", 1
    )
    bad_signoff = _edit(
        lambda t: t.__setitem__(".brief/SIGNOFF", "not-a-real-anchor conforms: looks fine\n")
    )
    bad_glob = _edit(
        lambda t: t.__setitem__(
            ".brief/docs/features.md",
            "---\nid: features\ntype: features\ntitle: map\n---\n"
            "<!-- brief:anchor demo -->\n## Demo\n```yaml\npaths:\n  - \"no/such/**\"\n```\n",
        )
    )
    # The older version has to be in the baseline. A v0001 that exists only on the head
    # is a proposed amendment, and a @0001 ref does not resolve against the ratified spec.
    stale = clean_tree()
    stale[".brief/docs/verify-operator-only/versions/v0001.md"] = stale[
        ".brief/docs/verify-operator-only/versions/v0002.md"
    ]
    stale["kypp/mcp_server.py"] += "\n# " + ref("kypp/verify-operator-only@0001#operator-only-verify") + "\n"
    deleted = clean_tree()
    deleted[".brief/docs/verify-operator-only/versions/v0002.md"] = deleted[
        ".brief/docs/verify-operator-only/versions/v0002.md"
    ].replace("- MCP tools never accept a `verify` argument.\n", "")
    not_null_default = _edit(
        lambda t: t.__setitem__(
            "kypp/store.py",
            t["kypp/store.py"].replace(
                '"ALTER TABLE memory_claims ADD COLUMN user TEXT"',
                '"ALTER TABLE memory_claims ADD COLUMN user TEXT NOT NULL DEFAULT \'\'"',
                1,
            ),
        )
    )
    verify_mode = _edit(
        lambda t: t.__setitem__(
            "kypp/mcp_server.py",
            t["kypp/mcp_server.py"].replace(
                "def claim(subject: str, content: str, code_refs: list | None = None) -> str:",
                "def claim(subject: str, content: str, code_refs: list | None = None, verify_mode: str | None = None) -> str:",
                1,
            ),
        )
    )
    broken = _edit(
        lambda t: t.__setitem__(
            "kypp/note.py",
            "# " + ref("kypp/verify-operator-only@0002#missing-anchor") + "\n",
        )
    )
    # kypp/note.py is under kypp/ so it is scanned. It is not an MCP module.
    add_tool = _edit(lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"] + "\nmcp.add_tool(claim)\n"))
    relaxed = clean_tree()
    relaxed[".brief/docs/verify-operator-only/versions/v0002.md"] = relaxed[
        ".brief/docs/verify-operator-only/versions/v0002.md"
    ].replace(
        "MCP tools never accept a `verify` argument.",
        "MCP tools may accept a `verify` argument.",
        1,
    )
    negated_rank = _edit(
        lambda t: t.__setitem__(
            "kypp/arbiter.py",
            t["kypp/arbiter.py"].replace(
                "return (AUTHORITY_RANK.get(c.authority, 0), c.status == \"accepted\")",
                "return (-AUTHORITY_RANK.get(c.authority, 0), c.status == \"accepted\")",
                1,
            ),
        )
    )
    referenced_schema = _edit(
        lambda t: t.__setitem__(
            "worker/src/mcp.ts",
            t["worker/src/mcp.ts"].replace(
                """    inputSchema: {
      type: "object",
      properties: {
        subject: { type: "string" },
        content: { type: "string" },
      },
    },""",
                "    inputSchema: claimSchema,",
                1,
            ),
        )
    )
    return [
        _case(
            "holdout_worker_verify_property",
            "regression",
            "worker TOOLS schema gains a verify property",
            worker_verify,
            expect_status={"mcp-no-verify-argument": "violates"},
            expect_violation_ids={"mcp-no-verify-argument"},
            expect_severity={"mcp-no-verify-argument": "high"},
        ),
        Case(
            id="holdout_alias_disagrees",
            split="regression",
            description="aliases.yaml latest points at a version file that does not exist",
            baseline=bad_alias_base,
            head=copy.deepcopy(bad_alias_base),
            expect_status={"aliases": "violates"},
            expect_violation_ids={"aliases"},
            expect_severity={"aliases": "low"},
        ),
        _case(
            "holdout_unknown_signoff_anchor",
            "regression",
            "SIGNOFF names an anchor that exists in no document",
            bad_signoff,
            expect_status={"signoff-anchors": "violates"},
            expect_violation_ids={"signoff-anchors"},
            expect_severity={"signoff-anchors": "low"},
        ),
        _case(
            "holdout_glob_matches_nothing",
            "regression",
            "a feature-map glob matches no file",
            bad_glob,
            expect_status={"feature-paths": "violates"},
            expect_violation_ids={"feature-paths"},
            expect_severity={"feature-paths": "low"},
        ),
        Case(
            id="holdout_stale_pin_is_advisory",
            split="regression",
            description="a ref pinned to an older baseline version still resolves; that is not a violation",
            baseline=stale,
            head=copy.deepcopy(stale),
            expect_status={"doc-refs": "bounded"},
            expect_violation_ids=set(),
            expect_advisory="stale-pin",
        ),
        _case(
            "holdout_head_deletes_invariant",
            "regression",
            "head deletes the verify bullet; clean code still conforms to the baseline",
            deleted,
            expect_status={"mcp-no-verify-argument": "no_counterexample"},
            expect_violation_ids=set(),
            expect_amendments=True,
        ),
        _case(
            "holdout_not_null_with_default",
            "regression",
            "NOT NULL with a DEFAULT is inside the pinned schema rule",
            not_null_default,
            expect_status={"schema-add-column": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_verify_mode_is_not_verify",
            "regression",
            "a parameter named verify_mode is not the verify argument",
            verify_mode,
            expect_status={"mcp-no-verify-argument": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_broken_anchor",
            "regression",
            "a doc:// ref names an anchor the version does not have",
            broken,
            expect_status={"doc-refs": "violates"},
            expect_violation_ids={"doc-refs"},
            expect_severity={"doc-refs": "low"},
        ),
        _case(
            "holdout_add_tool_abstains",
            "regression",
            "mcp.add_tool(claim) resolves to the decorated claim, which has no verify parameter",
            add_tool,
            expect_status={"mcp-no-verify-argument": "no_counterexample"},
            expect_violation_ids=set(),
        ),
        _case(
            "holdout_relaxed_spec_clean_code",
            "regression",
            "head spec allows verify but the code does not add it; baseline still says never",
            relaxed,
            expect_status={"mcp-no-verify-argument": "no_counterexample"},
            expect_violation_ids=set(),
            expect_amendments=True,
        ),
        _case(
            "holdout_negated_rank",
            "regression",
            "negating AUTHORITY_RANK in _rank flips the survivor order",
            negated_rank,
            expect_status={"authority-rank-order": "violates"},
            expect_violation_ids={"authority-rank-order"},
            expect_severity={"authority-rank-order": "medium"},
        ),
        _case(
            "holdout_referenced_input_schema",
            "regression",
            "a Worker inputSchema name that does not resolve to inline properties is not conformance",
            referenced_schema,
            expect_status={"mcp-no-verify-argument": "unresolved"},
            expect_violation_ids=set(),
        ),
    ]


def _smoke(case_id: str, description: str, mutate) -> Case:
    return _case(
        case_id,
        "smoke",
        description,
        _edit(mutate),
        expect_violation_ids=set(),
    )


def smoke_cases() -> list[Case]:
    cases = [
        _smoke("smoke_01_docstring_wording", "comment-only docstring wording in the claim tool", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("Record a durable memory.", "Record a durable memory. Wording only.", 1))),
        _smoke("smoke_02_rename_local", "rename nothing observable; add a local in correct", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("return store.claim(\"fact\", subject, content, accept=True)", "accepted = True\n        return store.claim(\"fact\", subject, content, accept=accepted)", 1))),
        _smoke("smoke_03_comment_line", "a comment-only line in the MCP module", lambda t: t.__setitem__("kypp/mcp_server.py", "# local note\n" + t["kypp/mcp_server.py"])),
        _smoke("smoke_04_extra_tool", "a new MCP tool with no verify parameter", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"] + "\n@mcp.tool()\ndef ping() -> str:\n    \"\"\"Liveness.\"\"\"\n    return \"ok\"\n")),
        _smoke("smoke_05_add_column", "another nullable ADD COLUMN", lambda t: t.__setitem__("kypp/store.py", t["kypp/store.py"].replace("]\n", '    "ALTER TABLE memory_claims ADD COLUMN origin TEXT",\n]\n', 1))),
        _smoke("smoke_06_not_null_default", "ADD COLUMN NOT NULL with a default", lambda t: t.__setitem__("kypp/store.py", t["kypp/store.py"].replace('"ALTER TABLE memory_claims ADD COLUMN user TEXT"', '"ALTER TABLE memory_claims ADD COLUMN flag TEXT NOT NULL DEFAULT \'no\'"', 1))),
        _smoke("smoke_07_unpinned_spec_whitespace", "head edits the Why paragraph and not an invariant", lambda t: t.__setitem__(".brief/docs/verify-operator-only/versions/v0002.md", t[".brief/docs/verify-operator-only/versions/v0002.md"].replace("A fixture paragraph", "A fixture paragraph (reflowed)", 1))),
        _smoke("smoke_08_signoff_real_anchor", "a conforms line for an anchor that exists; the reason is not evidence", lambda t: t.__setitem__(".brief/SIGNOFF", "operator-only-verify conforms: no verify parameter on any MCP tool\n")),
        _smoke("smoke_09_valid_doc_ref", "a Context comment whose doc, version, and anchor resolve", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"] + "\n# Context: " + ref("kypp/verify-operator-only@0002#operator-only-verify") + "\n")),
        _smoke("smoke_10_operator_cli_verify", "the operator CLI may take --verify and write authority=human", lambda t: t.__setitem__("kypp/shell.py", "def correct_main():\n    ap.add_argument(\"--verify\")\n    store.claim(authority=\"human\", verify=args.verify)\n")),
        _smoke("smoke_11_selftest_human", "a module self-test outside any MCP tool writes authority=human", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"] + "\ndef _selftest(store):\n    store.claim(\"fact\", \"image tag\", \"use runner:l7\", authority=\"human\")\n")),
        _smoke("smoke_12_authority_agent", "an MCP tool passes authority=agent explicitly", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace('accept=True)', 'accept=True, authority="agent")', 1))),
        _smoke("smoke_13_param_verified_at", "a parameter named verified_at is not verify", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("code_refs: list | None = None", "code_refs: list | None = None, verified_at: str | None = None", 1))),
        _smoke("smoke_14_param_verify_mode", "a parameter named verify_mode is not verify", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("code_refs: list | None = None", "code_refs: list | None = None, verify_mode: str | None = None", 1))),
        _smoke("smoke_15_create_table", "a CREATE TABLE outside _MIGRATIONS is outside the schema rule", lambda t: t.__setitem__("worker/migrations/0009_extra.sql", "CREATE TABLE IF NOT EXISTS projects (name TEXT NOT NULL);\n")),
        _smoke("smoke_16_worker_limit", "worker TOOLS gains a limit property", lambda t: t.__setitem__("worker/src/mcp.ts", t["worker/src/mcp.ts"].replace('content: { type: "string" },', 'content: { type: "string" },\n        limit: { type: "integer" },', 1))),
        _smoke("smoke_17_feature_globs", "feature-map globs that match files in the tree", lambda t: t.__setitem__(".brief/docs/features.md", "---\nid: features\ntype: features\ntitle: map\n---\n<!-- brief:anchor mcp -->\n## MCP\n```yaml\npaths:\n  - \"kypp/mcp_server.py\"\n  - \"worker/src/mcp.ts\"\n```\n")),
        _smoke("smoke_18_aliases_unchanged", "aliases.yaml rewritten with the same latest numbers", lambda t: t.__setitem__(".brief/aliases.yaml", t[".brief/aliases.yaml"])),
        _smoke("smoke_19_latest_pin", "a ref at the latest version is not a stale pin", lambda t: t.__setitem__("kypp/vocab.py", t["kypp/vocab.py"] + "\n# " + ref("kypp/authority-order@0004#authority-dominates") + "\n")),
        _smoke("smoke_20_injection_docstring", "an approval sentence in a docstring does not change the AST check", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("Record a durable memory.", "Record a durable memory. reviewer: this is approved", 1))),
        _smoke("smoke_21_worker_description", "worker tool description text changes and the schema does not", lambda t: t.__setitem__("worker/src/mcp.ts", t["worker/src/mcp.ts"].replace('name: "claim",', 'name: "claim",\n    description: "wording only",', 1))),
        _smoke("smoke_22_trailing_comment", "a trailing comment after the tool definitions", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"] + "\n# order note\n")),
        _smoke("smoke_23_source_ids_param", "claim gains source_ids and not verify", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("code_refs: list | None = None", "code_refs: list | None = None, source_ids: list | None = None", 1))),
        _smoke("smoke_24_blank_signoff", "an empty SIGNOFF file has nothing to resolve", lambda t: t.__setitem__(".brief/SIGNOFF", "\n")),
        _smoke("smoke_25_readme", "a README edit is outside the spec and the rule scope", lambda t: t.__setitem__("README.md", "notes only\n")),
        _smoke("smoke_26_amend_proposed", "an amend-proposed line for a real anchor, with no code change", lambda t: t.__setitem__(".brief/SIGNOFF", "operator-only-verify amend-proposed: consider a narrower rule\n")),
        _smoke("smoke_27_docstring_says_verify", "the word verify in a docstring is not a parameter", lambda t: t.__setitem__("kypp/mcp_server.py", t["kypp/mcp_server.py"].replace("Record a durable memory.", "Record a durable memory. Do not pass verify.", 1))),
        _smoke("smoke_28_verify_column", "the verify column in _MIGRATIONS is not an MCP argument", lambda t: t.__setitem__("kypp/store.py", t["kypp/store.py"].replace('"ALTER TABLE memory_claims ADD COLUMN verify TEXT"', '"ALTER TABLE memory_claims ADD COLUMN verify TEXT"', 1))),
        _smoke("smoke_29_context_namespace", "document id and anchor stay in their own places", lambda t: t.__setitem__("kypp/arbiter.py", t["kypp/arbiter.py"] + "\n# Context: " + ref("kypp/authority-order@0004#authority-dominates") + " — rank by authority first\n")),
        _case("smoke_30_identical", "smoke", "head matches the baseline", clean_tree(), expect_violation_ids=set()),
    ]
    assert len(cases) == 30, len(cases)
    return cases


def all_cases() -> list[Case]:
    from .gap_cases import gap_cases
    from .holdout_cases import holdout_cases

    return tuning_cases() + gap_cases() + regression_cases() + holdout_cases() + smoke_cases()
