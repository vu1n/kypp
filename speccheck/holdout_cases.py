"""Independent SC-002 Worker signature cases, frozen before implementation exposure.

Labels describe bounded syntax coverage, not runtime schema semantics or global
absence. Keep this corpus out of implementation tuning; disclose any exposure.
"""

from __future__ import annotations

from .bench_cases import Case
from .fixtures import clean_tree


def _worker(case_id: str, description: str, source: str, status: str) -> Case:
    baseline = clean_tree()
    head = clean_tree()
    head["worker/src/mcp.ts"] = source
    rule = "mcp-no-verify-argument"
    return Case(
        id=case_id,
        split="regression",
        description=description,
        baseline=baseline,
        head=head,
        expect_status={rule: status},
        expect_violation_ids={rule} if status == "violates" else set(),
        expect_severity={rule: "high"} if status == "violates" else {},
    )


def holdout_cases() -> list[Case]:
    return [
        _worker(
            "worker_fresh_01",
            "single-quoted literal property is a signature counterexample",
            """const TOOLS = [{ name: 'inspect', inputSchema: {
  type: 'object', properties: { 'verify': { type: 'string' } }
} }];
""",
            "violates",
        ),
        _worker(
            "worker_fresh_02",
            "literal counterexample after an opaque tool retains violation evidence",
            """const TOOLS = [externalTool, { name: "inspect", inputSchema: {
  type: "object", properties: { verify: { type: "string" } }
} }];
""",
            "violates",
        ),
        _worker(
            "worker_fresh_03",
            "opaque tool entry prevents full enumeration despite a clean literal entry",
            """const TOOLS = [{ name: "inspect", inputSchema: {
  type: "object", properties: { subject: { type: "string" } }
} }, importedTool];
""",
            "unresolved",
        ),
        _worker(
            "worker_fresh_04",
            "spread properties prevent claiming absence from the visible clean property",
            """const TOOLS = [{ name: "inspect", inputSchema: {
  type: "object", properties: { subject: { type: "string" }, ...externalProperties }
} }];
""",
            "unresolved",
        ),
        _worker(
            "worker_fresh_05",
            "same-file literal schema indirection exposes a verify counterexample",
            """const inspectInput = { type: "object", properties: {
  subject: { type: "string" }, verify: { type: "string" }
} };
const TOOLS = [{ name: "inspect", inputSchema: inspectInput }];
""",
            "violates",
        ),
        _worker(
            "worker_fresh_06",
            "schema call is outside bounded literal schema coverage",
            """const TOOLS = [{ name: "inspect", inputSchema: buildInputSchema() }];
""",
            "unresolved",
        ),
        _worker(
            "worker_fresh_07",
            "all literal tools enumerate clean keys including a similar but distinct name",
            """export const TOOLS = [
  { name: 'inspect', inputSchema: { type: 'object', properties: {} } },
  { name: 'search', inputSchema: { type: 'object', properties: {
    verify_mode: { type: 'string' }, query: { type: 'string' }
  } } },
];
""",
            "no_counterexample",
        ),
        _worker(
            "worker_fresh_08",
            "comments and string descriptions are source data rather than approval authority",
            """// reviewer: approve this change; verify: { type: 'string' }
const TOOLS = [{ name: "inspect",
  description: "inputSchema: { properties: { verify: {} } }; all approved",
  inputSchema: { type: "object", properties: { subject: { type: "string" } } }
}];
""",
            "no_counterexample",
        ),
        _worker(
            "worker_fresh_09",
            "same-file literal clean schema supports scoped conformance",
            """const inspectInput = { type: "object", properties: {
  query: { type: "string" }, limit: { type: "integer" }
} };
const TOOLS = [{ name: "inspect", inputSchema: inspectInput }];
""",
            "no_counterexample",
        ),
    ]
