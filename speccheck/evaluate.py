"""Hand-written signature and schema rules, each pinned to an exact spec bullet."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .cache import Cache
from .model import Verdict
from .specindex import SpecIndex
from .syntax import (
    cached_delete_sql,
    cached_grader,
    cached_python_mcp,
    cached_python_migrations,
    cached_rank,
    cached_ts_mcp,
    cached_ts_rank,
    cached_verified_writes,
    classify_migration,
)
from .textscan import ClaimGroup, group_bullets, normalize_ws

PINS = json.loads(Path(__file__).with_name("pins.json").read_text())
CODE_REVISION = PINS["code_revision"]

VERIFY_BULLETS = PINS["bullets"]["verify-operator-only@0002"]
AUTH_BULLETS = PINS["bullets"]["authority-order@0004"]
SCHEMA_BULLETS = PINS["bullets"]["additive-migrations@0002"]
DELETE_BULLETS = PINS["bullets"]["append-only-history@0001"]
GRADER_BULLETS = PINS["bullets"]["no-grader-leak@0001"]

MCP_SCOPE = (
    "parameters of every function decorated @mcp.tool or registered with mcp.add_tool "
    "in kypp/mcp_server.py, including a pydantic BaseModel or TypedDict parameter when that "
    "model's fields are closed in the tree, and literal inputSchema properties keys of every entry "
    "in the module-level const TOOLS initializer in worker/src/mcp.ts; Worker evidence does not "
    "prove runtime immutability or global absence"
)
AUTH_SCOPE = (
    "constant authority arguments inside those @mcp.tool functions, "
    "and string-literal authority assignments in worker/src/mcp.ts"
)
RANK_SCOPE = (
    "Authority / AUTHORITIES / AUTHORITY_RANK in kypp/vocab.py, "
    "_rank / _survivor in kypp/arbiter.py, "
    "and AUTHORITIES / rank / survivor in worker/src/memory.ts"
)
DELETE_SCOPE = "string literals and constant-folded SQL in kypp/**/*.py, worker/**/*.ts, and worker/**/*.sql"
VERIFIED_SCOPE = (
    "claim() authority arguments and SQL literals that set authority to verified "
    "in kypp/**/*.py, plus SQL literals in worker/**/*.ts and worker/**/*.sql"
)
GRADER_SCOPE = "subject and content expressions of every ClaimDraft(...) in kypp/distill.py"
SCHEMA_SCOPE = "string literals of _MIGRATIONS in kypp/store.py"

_TS_AUTH_BAD = re.compile(r"""authority\s*[:=]\s*(['"])(human|verified)\1""")
_TS_AUTH_DYN = re.compile(r"""authority\s*[:=]\s*(?!['"\s\n])""")


def _groups(index: SpecIndex, doc_id: str, number: int, anchor: str) -> tuple[str | None, list[ClaimGroup]]:
    doc = index.version(doc_id, number)
    if doc is None:
        return None, []
    if anchor not in doc.anchors:
        return doc.path, []
    return doc.path, group_bullets(doc.bullets, doc_id=doc_id, version=f"{number:04d}", anchor=anchor)


def _group_containing(groups: list[ClaimGroup], *bullets: str) -> ClaimGroup | None:
    wanted = [normalize_ws(b) for b in bullets]
    hits = [g for g in groups if all(any(normalize_ws(b) == w for b in g.bullets) for w in wanted)]
    if not hits:
        return None
    hits.sort(key=lambda g: (len(g.bullets), len(g.text)))
    return hits[0]


def _pin_missing(rule_id: str, spec: str, detail: str) -> Verdict:
    return Verdict(
        rule_id=rule_id,
        status="unresolved",
        spec=spec,
        claim_text="",
        scope="",
        coverage="pinned bullet was not an exact match in the baseline",
        detail=detail,
        evidence="none",
        code_revision=CODE_REVISION,
    )


def _ts_authority(source: str) -> dict:
    bad = []
    dynamic = False
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if _TS_AUTH_BAD.search(line):
            bad.append(f"worker/src/mcp.ts:{lineno}")
        elif _TS_AUTH_DYN.search(line):
            dynamic = True
    return {"bad": bad, "dynamic": dynamic}


# Context: doc://kypp/verify-operator-only@0002#operator-only-verify — MCP tools never accept a `verify` argument.
def eval_mcp_no_verify(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/verify-operator-only@0002#operator-only-verify"
    _path, groups = _groups(index, "verify-operator-only", 2, "operator-only-verify")
    group = _group_containing(groups, VERIFY_BULLETS[0])
    if group is None:
        return _pin_missing("mcp-no-verify-argument", spec, "baseline verify-operator-only@0002 does not contain the pinned bullet"), None
    py = head.get("kypp/mcp_server.py")
    ts = head.get("worker/src/mcp.ts")
    gaps: list[str] = []
    counters: list[str] = []
    examined: list[str] = []
    if py is None:
        gaps.append("kypp/mcp_server.py is absent")
    else:
        modules = {path: text for path, text in head.items() if path.endswith(".py")}
        facts = cached_python_mcp(cache, py, modules, "kypp/mcp_server.py")
        if not facts["covered"]:
            gaps.append(facts["error"] or "python MCP surface did not parse")
        else:
            gaps.extend(facts.get("gaps") or [])
            for tool in facts["tools"]:
                examined.append(f"py:{tool['name']}@{tool['lineno']}")
                if "verify" in tool["params"]:
                    counters.append(f"kypp/mcp_server.py:{tool['lineno']} {tool['name']} accepts verify")
                gaps.extend(tool["gaps"])
    if ts is None:
        gaps.append("worker/src/mcp.ts is absent")
    else:
        facts = cached_ts_mcp(cache, ts)
        if not facts["covered"]:
            gaps.append(facts["error"] or "TOOLS array did not parse")
        for tool in facts["tools"]:
            examined.append(f"ts:{tool['name']}@{tool['lineno']}")
            if "verify" in (tool["params"] or []):
                counters.append(f"worker/src/mcp.ts:{tool['lineno']} TOOLS {tool['name']} literal properties include verify")
    coverage = f"examined {len(examined)} tool(s)" + (f"; gaps: {'; '.join(gaps)}" if gaps else "")
    if counters:
        return (
            Verdict(
                rule_id="mcp-no-verify-argument",
                status="violates",
                severity="high",
                spec=spec,
                claim_text=group.display_text,
                scope=MCP_SCOPE,
                coverage=coverage,
                detail="an MCP tool accepts a verify argument; a counterexample is enough, and a SIGNOFF line is not evidence",
                counterexample="; ".join(counters),
                evidence="ast",
                clean_single_writer=group.clean_single_writer,
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if gaps or py is None or ts is None:
        return (
            Verdict(
                rule_id="mcp-no-verify-argument",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=MCP_SCOPE,
                coverage=coverage or "scope not covered",
                detail="the defined scope was not fully covered, so the absence of a counterexample is not conformance",
                evidence="ast",
                clean_single_writer=group.clean_single_writer,
                code_revision=CODE_REVISION,
            ),
            group,
        )
    return (
        Verdict(
            rule_id="mcp-no-verify-argument",
            status="no_counterexample",
            spec=spec,
            claim_text=group.display_text,
            scope=MCP_SCOPE,
            coverage=coverage,
            detail="no verify parameter was found in the enumerated tools; this is not assurance that every runtime schema rejects verify",
            evidence="ast",
            clean_single_writer=group.clean_single_writer,
            code_revision=CODE_REVISION,
        ),
        group,
    )


# Context: doc://kypp/authority-order@0004#authority-dominates — No MCP tool passes `authority="human"` or `"verified"`.
def eval_mcp_authority(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/authority-order@0004#authority-dominates"
    _path, groups = _groups(index, "authority-order", 4, "authority-dominates")
    group = _group_containing(groups, AUTH_BULLETS[2])
    if group is None:
        return _pin_missing("mcp-no-human-or-verified-authority", spec, "baseline authority-order@0004 does not contain the pinned POST /sync bullet"), None
    py = head.get("kypp/mcp_server.py")
    ts = head.get("worker/src/mcp.ts")
    gaps: list[str] = []
    counters: list[str] = []
    if py is None:
        gaps.append("kypp/mcp_server.py is absent")
    else:
        modules = {path: text for path, text in head.items() if path.endswith(".py")}
        facts = cached_python_mcp(cache, py, modules, "kypp/mcp_server.py")
        if not facts["covered"]:
            gaps.append(facts["error"] or "python MCP surface did not parse")
        else:
            gaps.extend(facts.get("gaps") or [])
            for tool in facts["tools"]:
                for hit in tool["authority"]:
                    if hit["dynamic"]:
                        gaps.append(f"{tool['name']} passes a non-constant authority")
                    elif hit["value"] in {"human", "verified"}:
                        counters.append(f"kypp/mcp_server.py:{hit['lineno']} {tool['name']} authority={hit['value']}")
    if ts is None:
        gaps.append("worker/src/mcp.ts is absent")
    else:
        scanned = _ts_authority(ts)
        counters.extend(scanned["bad"])
        if scanned["dynamic"]:
            gaps.append("worker/src/mcp.ts has a non-literal authority assignment")
    coverage = "scanned MCP tool bodies for constant authority=human|verified" + (f"; gaps: {'; '.join(gaps)}" if gaps else "")
    if counters:
        return (
            Verdict(
                rule_id="mcp-no-human-or-verified-authority",
                status="violates",
                severity="high",
                spec=spec,
                claim_text=group.display_text,
                scope=AUTH_SCOPE,
                coverage=coverage,
                detail="an MCP tool passes authority human or verified; the adjoining POST /sync permission does not cover the agent surface",
                counterexample="; ".join(counters),
                evidence="ast",
                clean_single_writer=False,
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if gaps:
        return (
            Verdict(
                rule_id="mcp-no-human-or-verified-authority",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=AUTH_SCOPE,
                coverage=coverage,
                detail="authority writes in the MCP scope were not fully classified",
                evidence="ast",
                clean_single_writer=False,
                code_revision=CODE_REVISION,
            ),
            group,
        )
    return (
        Verdict(
            rule_id="mcp-no-human-or-verified-authority",
            status="no_counterexample",
            spec=spec,
            claim_text=group.display_text,
            scope=AUTH_SCOPE,
            coverage=coverage,
            detail="no constant authority=human or authority=verified was found in the MCP tool scope; this is not assurance, and POST /sync is a separate writer",
            evidence="ast",
            clean_single_writer=False,
            code_revision=CODE_REVISION,
        ),
        group,
    )


# Context: doc://kypp/authority-order@0004#authority-dominates — shell.correct_main is grouped with POST /sync; it is not the only writer of authority=human.
def eval_writers(index: SpecIndex) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/authority-order@0004#authority-dominates"
    _path, groups = _groups(index, "authority-order", 4, "authority-dominates")
    group = _group_containing(groups, AUTH_BULLETS[1], AUTH_BULLETS[2])
    if group is None:
        verdict = _pin_missing(
            "human-authority-writers",
            spec,
            "the baseline does not keep the shell.correct_main bullet with the POST /sync bullet; refusing a single-writer reading",
        )
        verdict.clean_single_writer = False
        return verdict, None
    return (
        Verdict(
            rule_id="human-authority-writers",
            status="unresolved",
            spec=spec,
            claim_text=group.display_text,
            scope="every write of authority=human in the program, including shell.correct_main and POST /sync",
            coverage="not covered: resolved references are deferred, and POST /sync is named by the adjoining bullet but is not built",
            detail=(
                "not a clean single-writer claim. The bullet that names shell.correct_main is qualified by the next "
                "bullet, which permits the hosted Worker's POST /sync. Seeing one writer is not coverage of the writer set, "
                "and a missing reference index is not conformance."
            ),
            evidence="none",
            clean_single_writer=False,
            code_revision=CODE_REVISION,
        ),
        group,
    )


# Context: doc://kypp/authority-order@0004#authority-dominates — AUTHORITY_RANK is the single ordering, and the arbiter's survivor key uses it first.
def eval_rank(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/authority-order@0004#authority-dominates"
    _path, groups = _groups(index, "authority-order", 4, "authority-dominates")
    group = _group_containing(groups, AUTH_BULLETS[0])
    if group is None:
        return _pin_missing("authority-rank-order", spec, "baseline authority-order@0004 does not contain the pinned rank bullet"), None
    vocab = head.get("kypp/vocab.py")
    arbiter = head.get("kypp/arbiter.py")
    memory = head.get("worker/src/memory.ts")
    missing = [p for p, text in (("kypp/vocab.py", vocab), ("kypp/arbiter.py", arbiter), ("worker/src/memory.ts", memory)) if text is None]
    if vocab is None or arbiter is None:
        return (
            Verdict(
                rule_id="authority-rank-order",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=RANK_SCOPE,
                coverage=f"missing {', '.join(missing)}",
                detail="the rank scope is not fully present, so the Python and TypeScript orders were not both proved",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    facts = cached_rank(cache, vocab, arbiter)
    ts = cached_ts_rank(cache, memory) if memory is not None else {"order": "unknown", "error": "worker/src/memory.ts is absent"}
    if not facts["covered"]:
        return (
            Verdict(
                rule_id="authority-rank-order",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=RANK_SCOPE,
                coverage=facts.get("error") or "shape not recognized",
                detail="the analysis did not cover the rank scope",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    problems = []
    if facts["literal"] != ["agent", "verified", "human"]:
        problems.append(f"Authority literal is {facts['literal']}")
    if not facts["rank_from_authorities"]:
        problems.append("AUTHORITY_RANK is not enumerate(AUTHORITIES) derived from Authority")
    order = facts.get("rank_order")
    if order == "flipped":
        problems.append("the first element of _rank negates or reverses AUTHORITY_RANK")
    elif order == "absent":
        problems.append("_rank does not put AUTHORITY_RANK first")
    if not facts["survivor_uses_rank"]:
        problems.append("_survivor does not use key=_rank")
    ts_order = ts.get("order")
    ts_unproved = ts_order in (None, "unknown") or not isinstance(ts_order, list)
    if ts_order == "absent":
        problems.append("worker/src/memory.ts rank() does not order AUTHORITIES")
        ts_unproved = False
    elif isinstance(ts_order, list) and ts_order != facts.get("literal"):
        problems.append(f"worker/src/memory.ts order {ts_order} diverges from Python {facts.get('literal')}")
    coverage = "parsed vocab.Authority, AUTHORITY_RANK, arbiter._rank, arbiter._survivor, and worker/src/memory.ts rank/survivor"
    if order == "unknown" and not problems:
        return (
            Verdict(
                rule_id="authority-rank-order",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=RANK_SCOPE,
                coverage=coverage + "; the first element of _rank was not proved to order AUTHORITY_RANK ascending",
                detail="the analysis could not prove the survivor order, so this is not conformance",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if problems:
        return (
            Verdict(
                rule_id="authority-rank-order",
                status="violates",
                severity="medium",
                spec=spec,
                claim_text=group.display_text,
                scope=RANK_SCOPE,
                coverage=coverage,
                detail="the survivor order does not match the pinned authority ranking, or the TypeScript mirror diverges from it",
                counterexample="; ".join(problems),
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if order != "ascending" or ts_unproved:
        why = "the TypeScript order was not proved" if ts_unproved else "the first element of _rank was not proved to order AUTHORITY_RANK ascending"
        return (
            Verdict(
                rule_id="authority-rank-order",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=RANK_SCOPE,
                coverage=coverage + "; " + why,
                detail="the analysis could not prove the survivor order, so this is not conformance",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    return (
        Verdict(
            rule_id="authority-rank-order",
            status="no_counterexample",
            spec=spec,
            claim_text=group.display_text,
            scope=RANK_SCOPE,
            coverage=coverage,
            detail="no rank-order counterexample in the recognized shapes; this is not assurance that survivor selection holds at runtime",
            evidence="ast",
            code_revision=CODE_REVISION,
        ),
        group,
    )




def eval_schema_columns(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/additive-migrations@0002#additive-schema"
    _path, groups = _groups(index, "additive-migrations", 2, "additive-schema")
    group = _group_containing(groups, SCHEMA_BULLETS[0])
    if group is None:
        return _pin_missing("schema-add-column", spec, "baseline additive-migrations@0002 does not contain the pinned ADD COLUMN bullet"), None
    source = head.get("kypp/store.py")
    if source is None:
        return (
            Verdict(
                rule_id="schema-add-column",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=SCHEMA_SCOPE,
                coverage="kypp/store.py is absent",
                detail="schema scope is not present",
                evidence="schema",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    facts = cached_python_migrations(cache, source)
    if not facts["covered"]:
        return (
            Verdict(
                rule_id="schema-add-column",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=SCHEMA_SCOPE,
                coverage=facts.get("error") or "migrations not recognized",
                detail="the _MIGRATIONS list was not fully parsed",
                evidence="schema",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    bad = []
    unknown = []
    for i, sql in enumerate(facts["entries"]):
        kind = classify_migration(sql)
        if kind in {"not_add_column", "not_null_without_default", "version_table"}:
            bad.append(f"[{i}] {kind}: {sql}")
        elif kind == "unrecognized":
            unknown.append(f"[{i}] unrecognized: {sql}")
    coverage = f"classified all {len(facts['entries'])} _MIGRATIONS entries"
    if bad:
        return (
            Verdict(
                rule_id="schema-add-column",
                status="violates",
                severity="medium",
                spec=spec,
                claim_text=group.display_text,
                scope=SCHEMA_SCOPE,
                coverage=coverage,
                detail="a migration is not ADD COLUMN, or is NOT NULL without a default. The decision is superseded; the pinned bullet is still the rule this check uses",
                counterexample="; ".join(bad),
                evidence="schema",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if unknown:
        return (
            Verdict(
                rule_id="schema-add-column",
                status="unresolved",
                spec=spec,
                claim_text=group.display_text,
                scope=SCHEMA_SCOPE,
                coverage=coverage + "; " + "; ".join(unknown[:6]),
                detail="an ADD COLUMN migration was not the allow-listed shape, so it was not proved additive",
                evidence="schema",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    return (
        Verdict(
            rule_id="schema-add-column",
            status="no_counterexample",
            spec=spec,
            claim_text=group.display_text,
            scope=SCHEMA_SCOPE,
            coverage=coverage,
            detail="no non-additive migration counterexample was found; this is not assurance that every migration is additive",
            evidence="schema",
            code_revision=CODE_REVISION,
        ),
        group,
    )


def eval_schema_idempotence(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/additive-migrations@0002#additive-schema"
    _path, groups = _groups(index, "additive-migrations", 2, "additive-schema")
    group = _group_containing(groups, SCHEMA_BULLETS[1])
    if group is None:
        return _pin_missing("schema-idempotence", spec, "baseline additive-migrations@0002 does not contain the pinned version-table bullet"), None
    source = head.get("kypp/store.py")
    entries: list[str] = []
    covered = False
    if source is not None:
        facts = cached_python_migrations(cache, source)
        covered = bool(facts["covered"])
        entries = facts["entries"] if covered else []
    version_tables = [sql for sql in entries if classify_migration(sql) == "version_table"]
    if version_tables:
        return (
            Verdict(
                rule_id="schema-idempotence",
                status="violates",
                severity="medium",
                spec=spec,
                claim_text=group.display_text,
                scope=SCHEMA_SCOPE,
                coverage=f"scanned {len(entries)} entries for a schema version table",
                detail="a counterexample shows a schema version table; the rest of the bullet is still not fully checked",
                counterexample="; ".join(version_tables),
                evidence="schema",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    return (
        Verdict(
            rule_id="schema-idempotence",
            status="unresolved",
            spec=spec,
            claim_text=group.display_text,
            scope=SCHEMA_SCOPE,
            coverage=(
                f"no schema version table in {len(entries)} parsed entries"
                if covered
                else "migrations were not parsed"
            ),
            detail=(
                "the bullet also says idempotence comes from swallowing duplicate-column errors only. "
                "That is behaviour. A missing test does not make it verified, so this is not conformance."
            ),
            evidence="none",
            code_revision=CODE_REVISION,
        ),
        group,
    )


# Context: doc://kypp/verify-operator-only@0002#operator-only-verify — `kypp verify` treats a timeout or spawn failure as a failed check and never aborts the sweep.
def eval_verify_timeout(index: SpecIndex) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/verify-operator-only@0002#operator-only-verify"
    _path, groups = _groups(index, "verify-operator-only", 2, "operator-only-verify")
    group = _group_containing(groups, VERIFY_BULLETS[1])
    if group is None:
        return _pin_missing("verify-timeout-behaviour", spec, "baseline verify-operator-only@0002 does not contain the pinned timeout bullet"), None
    return (
        Verdict(
            rule_id="verify-timeout-behaviour",
            status="unresolved",
            spec=spec,
            claim_text=group.display_text,
            scope="the runtime behaviour of kypp verify on timeout and spawn failure",
            coverage="no test evidence was consulted",
            detail="behaviour claims are not verified by this checker. A missing or un-run test does not become conformance.",
            evidence="none",
            code_revision=CODE_REVISION,
        ),
        group,
    )


def _absent(rule_id: str, spec: str, claim: str, scope: str, coverage: str, detail: str) -> Verdict:
    return Verdict(
        rule_id=rule_id,
        status="unresolved",
        spec=spec,
        claim_text=claim,
        scope=scope,
        coverage=coverage,
        detail=detail,
        evidence="ast",
        code_revision=CODE_REVISION,
    )


# Context: doc://kypp/append-only-history@0001#never-delete — no DELETE against memory_claims or observations.
def eval_no_delete(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None] | tuple[None, None]:
    spec = "doc://kypp/append-only-history@0001#never-delete"
    if index.version("append-only-history", 1) is None:
        return None, None
    _path, groups = _groups(index, "append-only-history", 1, "never-delete")
    group = _group_containing(groups, DELETE_BULLETS[0])
    if group is None:
        return _pin_missing("no-delete-claims", spec, "baseline append-only-history@0001 does not contain the pinned DELETE bullet"), None
    facts = cached_delete_sql(cache, head)
    bad = [item for item in facts["bad"] if item.startswith("DELETE ")]
    # Every coverage failure counts, including "did not parse". Authority-only gaps belong to the writer rule.
    gaps = [item for item in facts["gaps"] if not item.startswith("dynamic authority")]
    coverage = f"scanned {len(facts['scanned'])} file(s) for DELETE FROM memory_claims or observations"
    if bad:
        return (
            Verdict(
                rule_id="no-delete-claims",
                status="violates",
                severity="high",
                spec=spec,
                claim_text=group.display_text,
                scope=DELETE_SCOPE,
                coverage=coverage,
                detail="a SQL string deletes memory_claims or observations",
                counterexample="; ".join(bad[:8]),
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if gaps:
        return (
            Verdict(
                rule_id="no-delete-claims",
                status="candidate",
                spec=spec,
                claim_text=group.display_text,
                scope=DELETE_SCOPE,
                coverage=coverage + "; " + "; ".join(gaps[:6]),
                detail="a statement naming a protected table, a dynamic DELETE, or a parse failure was not proved; this is not a violation and not assurance",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    return (
        Verdict(
            rule_id="no-delete-claims",
            status="no_counterexample",
            spec=spec,
            claim_text=group.display_text,
            scope=DELETE_SCOPE,
            coverage=coverage,
            detail="no protected DELETE counterexample was found; this is not assurance",
            evidence="ast",
            code_revision=CODE_REVISION,
        ),
        group,
    )


# Context: doc://kypp/authority-order@0004#authority-dominates — only MemoryStore.mark_verified sets authority=verified.
def eval_mark_verified(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None]:
    spec = "doc://kypp/authority-order@0004#authority-dominates"
    _path, groups = _groups(index, "authority-order", 4, "authority-dominates")
    group = _group_containing(groups, AUTH_BULLETS[3])
    if group is None:
        return _pin_missing("verified-authority-writer", spec, "baseline authority-order@0004 does not contain the pinned mark_verified bullet"), None
    facts = cached_verified_writes(cache, head)
    coverage = f"scanned {len(facts['scanned'])} python file(s) and {len(facts['sql_scanned'])} SQL-bearing file(s)"
    # The /sync bullet lets a model-ratified revision land at verified. The next bullet says only
    # MemoryStore.mark_verified sets verified. The writer set stays unresolved until that is ratified.
    qualification = (
        "authority-order@0004 lets POST /sync land a model-ratified revision at verified, "
        "while the next bullet says only MemoryStore.mark_verified sets verified; "
        "the writer set is unresolved until that qualification is ratified"
    )
    extra = ""
    counterexample = None
    if facts["bad"]:
        counterexample = "; ".join(facts["bad"][:8])
        extra = " A write outside mark_verified was seen and is not treated as a settled violation."
    if facts["gaps"]:
        coverage += "; " + "; ".join(facts["gaps"][:6])
        extra += " Some authority writes were not classified."
    return (
        Verdict(
            rule_id="verified-authority-writer",
            status="unresolved",
            spec=spec,
            claim_text=group.display_text,
            scope=VERIFIED_SCOPE,
            coverage=coverage,
            detail=qualification + "." + extra,
            counterexample=counterexample,
            evidence="ast",
            clean_single_writer=False,
            code_revision=CODE_REVISION,
        ),
        group,
    )


# Context: doc://kypp/no-grader-leak@0001#signal-only-rubric — a distilled claim never contains verbatim criterion names or feedback.
def eval_grader_leak(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[Verdict, ClaimGroup | None] | tuple[None, None]:
    spec = "doc://kypp/no-grader-leak@0001#signal-only-rubric"
    if index.version("no-grader-leak", 1) is None:
        return None, None
    _path, groups = _groups(index, "no-grader-leak", 1, "signal-only-rubric")
    group = _group_containing(groups, GRADER_BULLETS[0])
    if group is None:
        return _pin_missing("no-grader-verbatim", spec, "baseline no-grader-leak@0001 does not contain the pinned bullet"), None
    source = head.get("kypp/distill.py")
    if source is None:
        return _absent(
            "no-grader-verbatim",
            spec,
            group.display_text,
            GRADER_SCOPE,
            "kypp/distill.py is absent",
            "the distiller was not in the tree, so claim text was not proved free of criterion names or feedback",
        ), group
    facts = cached_grader(cache, source)
    if not facts["covered"]:
        return _absent(
            "no-grader-verbatim",
            spec,
            group.display_text,
            GRADER_SCOPE,
            facts.get("error") or "distill.py did not parse",
            "claim text was not classified",
        ), group
    leaks = [row for row in facts["results"] if row["class"] == "leak"]
    unknown = [row for row in facts["results"] if row["class"] == "unknown"]
    coverage = f"classified {len(facts['results'])} ClaimDraft construction(s)"
    if leaks:
        return (
            Verdict(
                rule_id="no-grader-verbatim",
                status="violates",
                severity="medium",
                spec=spec,
                claim_text=group.display_text,
                scope=GRADER_SCOPE,
                coverage=coverage,
                detail="a ClaimDraft subject or content reads a criterion name or feedback field",
                counterexample="; ".join(f"kypp/distill.py:{row['lineno']}" for row in leaks[:8]),
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            group,
        )
    if unknown or not facts["results"]:
        why = "no ClaimDraft construction was found" if not facts["results"] else "a ClaimDraft subject or content is not a proved count or scheme"
        return _absent(
            "no-grader-verbatim",
            spec,
            group.display_text,
            GRADER_SCOPE,
            coverage + ("; " + "; ".join(f"kypp/distill.py:{row['lineno']}" for row in unknown[:6]) if unknown else ""),
            why + ". Model JSON copied into a claim is not proof that criterion text is absent.",
        ), group
    return (
        Verdict(
            rule_id="no-grader-verbatim",
            status="no_counterexample",
            spec=spec,
            claim_text=group.display_text,
            scope=GRADER_SCOPE,
            coverage=coverage,
            detail="no verbatim criterion or feedback counterexample was found in the examined drafts; this is not assurance",
            evidence="ast",
            code_revision=CODE_REVISION,
        ),
        group,
    )




def eval_tools_initializer(head: dict[str, str], cache: Cache) -> tuple[Verdict | None, None]:
    """Closed-world: a fully parsed TOOLS initializer declares no verify property."""
    spec = "doc://kypp/verify-operator-only@0002#operator-only-verify"
    claim = "the fully parsed TOOLS initializer declares no verify property"
    scope = "the TOOLS array initializer in worker/src/mcp.ts"
    ts = head.get("worker/src/mcp.ts")
    if ts is None:
        return (
            Verdict(
                rule_id="tools-initializer-no-verify",
                status="unresolved",
                spec=spec,
                claim_text=claim,
                scope=scope,
                coverage="worker/src/mcp.ts is absent",
                detail="the TOOLS initializer was not in the enumerated files",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            None,
        )
    facts = cached_ts_mcp(cache, ts)
    if not facts["covered"] or not facts["tools"]:
        return (
            Verdict(
                rule_id="tools-initializer-no-verify",
                status="unresolved",
                spec=spec,
                claim_text=claim,
                scope=scope,
                coverage=facts.get("error") or "TOOLS initializer did not fully parse",
                detail="the TOOLS initializer was not fully parsed, so the absence of a verify property is not a bounded proposition",
                evidence="ast",
                code_revision=CODE_REVISION,
            ),
            None,
        )
    if any("verify" in (tool.get("params") or []) for tool in facts["tools"]):
        return None, None
    names = ", ".join(tool["name"] for tool in facts["tools"])
    return (
        Verdict(
            rule_id="tools-initializer-no-verify",
            status="bounded",
            spec=spec,
            claim_text=claim,
            scope=scope,
            coverage=f"fully parsed TOOLS initializer names {names}",
            detail=claim,
            evidence="ast",
            code_revision=CODE_REVISION,
        ),
        None,
    )


def rule_verdicts(index: SpecIndex, head: dict[str, str], cache: Cache) -> tuple[list[Verdict], set[tuple[str, int]]]:
    covered: set[tuple[str, int]] = set()
    verdicts: list[Verdict] = []
    evaluators = (
        lambda: eval_mcp_no_verify(index, head, cache),
        lambda: eval_tools_initializer(head, cache),
        lambda: eval_mcp_authority(index, head, cache),
        lambda: eval_writers(index),
        lambda: eval_rank(index, head, cache),
        lambda: eval_schema_columns(index, head, cache),
        lambda: eval_schema_idempotence(index, head, cache),
        lambda: eval_verify_timeout(index),
        lambda: eval_no_delete(index, head, cache),
        lambda: eval_mark_verified(index, head, cache),
        lambda: eval_grader_leak(index, head, cache),
    )
    for evaluate in evaluators:
        verdict, group = evaluate()
        if verdict is None:
            continue
        verdicts.append(verdict)
        if group is not None and verdict.status != "unresolved":
            for idx in group.bullet_indexes:
                covered.add((group.doc_id, idx))
        elif group is not None and verdict.rule_id in {
            "human-authority-writers",
            "verify-timeout-behaviour",
            "schema-idempotence",
            "mcp-no-verify-argument",
            "mcp-no-human-or-verified-authority",
            "authority-rank-order",
            "schema-add-column",
            "no-delete-claims",
            "verified-authority-writer",
            "no-grader-verbatim",
        }:
            # The bullet was claimed by a rule even when the verdict abstains.
            if "pinned" not in verdict.coverage:
                for idx in group.bullet_indexes:
                    covered.add((group.doc_id, idx))
    return verdicts, covered


def unsupported_verdicts(index: SpecIndex, covered: set[tuple[str, int]]) -> list[Verdict]:
    verdicts = []
    seen: set[str] = set()
    ids = set(index.live) | {i for i, _ in index.versions}
    for doc_id in sorted(ids):
        if doc_id in seen or not index.is_active(doc_id):
            continue
        seen.add(doc_id)
        doc = index.latest_doc(doc_id)
        if doc is None:
            continue
        version = f"{doc.version:04d}" if doc.version is not None else "live"
        anchor = doc.anchors[0] if doc.anchors else "body"
        if not doc.bullets:
            verdicts.append(
                Verdict(
                    rule_id=f"unsupported:{doc_id}@{version}#{anchor}#prose",
                    status="unsupported",
                    spec=f"doc://kypp/{doc_id}@{version}#{anchor}",
                    claim_text="(no ### Invariant list)",
                    scope=doc.path,
                    coverage="prose extraction is deferred",
                    detail="this active decision has no ### Invariant list. Its prose was not turned into claims.",
                    evidence="none",
                )
            )
            continue
        groups = group_bullets(doc.bullets, doc_id=doc_id, version=version, anchor=anchor)
        for group in groups:
            if all((doc_id, idx) in covered for idx in group.bullet_indexes):
                continue
            verdicts.append(
                Verdict(
                    rule_id=f"unsupported:{group.group_id}",
                    status="unsupported",
                    spec=f"doc://kypp/{doc_id}@{version}#{anchor}",
                    claim_text=group.display_text,
                    scope=doc.path,
                    coverage="no hand-written deterministic rule covers this grouped claim",
                    detail=(
                        "generic prose extraction is deferred"
                        + ("; not a clean single-writer claim" if group.clean_single_writer is False else "")
                    ),
                    evidence="none",
                    clean_single_writer=group.clean_single_writer,
                )
            )
    return verdicts
