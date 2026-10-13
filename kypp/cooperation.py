"""Frozen v1 portable-cooperation contract, shared with worker/src/cooperation.ts.

# Context: doc://kypp/memory-scope-decay@0005#scope-keys-decay — cooperation records are not claims; wall-clock expiry never deletes, promotes, or corroborates them.
# Context: doc://kypp/agent-mcp-surface-compose@0002#compose-surface — append_context and query_context take no authority parameter and return a bounded memory contribution.

No store, no MCP registration, and no model call. Repository method names are
append, get_by_ids, and scan_candidates, the Python spellings of the Worker
CooperationRepository methods append, getByIds, and scanCandidates.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

SCHEMA_VERSION = 1
SELECTOR_VERSION = "cooperation-lexical-v1"
SAFE_INT = 9_007_199_254_740_991

COOPERATION_LIMITS = {
    "schema_version": SCHEMA_VERSION,
    "record_max_bytes": 8192,
    "string_max_bytes": 1024,
    "locator_max_bytes": 512,
    "source_refs_max": 16,
    "lineage_refs_max": 16,
    "ttl_default_seconds": 86_400,
    "ttl_min_seconds": 60,
    "ttl_max_seconds": 604_800,
    "result_default": 12,
    "result_min": 1,
    "result_max": 30,
    "query_text_max_bytes": 512,
    "response_default_bytes": 16_384,
    "response_min_bytes": 2048,
    "response_max_bytes": 32_768,
    "candidate_window": 100,
    "exact_ids_max": 30,
    "population_max": 10_000,
    "idempotency_key_max_bytes": 128,
    "diagnostics_max": 16,
}

COOPERATION_ERROR_CODES = (
    "INVALID_RECORD",
    "UNKNOWN_ORIGIN",
    "UNAUTHENTICATED",
    "IDEMPOTENCY_CONFLICT",
    "CAPACITY_EXCEEDED",
    "UNAVAILABLE",
)
COOPERATION_KINDS = ("handoff", "evidence_reference")
INCONCLUSIVE_REASONS = ("timeout", "spawn_failure", "evaluator_failure", "infrastructure", "legacy_false")
FORBIDDEN_FIELDS = (
    "access_url", "approval", "assignee", "authority", "bearer", "capability", "command",
    "credential", "grant", "measured", "password", "room", "secret", "signed_url", "status",
    "token", "transcript", "verifier", "verify",
)
_FORBIDDEN = set(FORBIDDEN_FIELDS)
_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_RECORD_ID = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_BRIEF = re.compile(r"^doc://[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*@[0-9]+#[A-Za-z0-9][A-Za-z0-9_-]*$")
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?$")


class CooperationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CooperationRepository(Protocol):
    """Same seam as worker CooperationRepository. Scan rows are already authorized."""

    def append(self, request: dict) -> dict: ...

    def get_by_ids(self, request: dict) -> list: ...

    def scan_candidates(self, request: dict) -> dict: ...


def utf8_bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if not -SAFE_INT <= value <= SAFE_INT:
            raise CooperationError("INVALID_RECORD", "canonical JSON only accepts safe integers")
        return str(value)
    if isinstance(value, float):
        raise CooperationError("INVALID_RECORD", "canonical JSON only accepts safe integers")
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value)
        return "{" + ",".join(_encode_string(key) + ":" + canonical_json(value[key]) for key in keys) + "}"
    raise CooperationError("INVALID_RECORD", "value cannot be canonicalized")


def _encode_string(value: str) -> str:
    out = ['"']
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise CooperationError("INVALID_RECORD", "unpaired surrogate")
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\b":
            out.append("\\b")
        elif char == "\f":
            out.append("\\f")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _fail(message: str) -> None:
    raise CooperationError("INVALID_RECORD", message)


def _object(value: object, allowed: tuple[str, ...] | list[str], label: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    for key in value:
        if key in _FORBIDDEN:
            _fail(f"forbidden field {key}")
        if key not in allowed:
            _fail(f"unknown field {key}")
    return value


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or isinstance(value, float) and not value.is_integer():
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        value = int(value)
    if not -SAFE_INT <= value <= SAFE_INT:
        return None
    return value


def _read_int(value: object, label: str, low: int, high: int) -> int:
    number = _integer(value)
    if number is None or number < low or number > high:
        _fail(f"{label} must be an integer from {low} to {high}")
    return number


def _controls_ok(value: str, narrative: bool) -> bool:
    for char in value:
        code = ord(char)
        if code in (0x09, 0x0A):
            if not narrative:
                return False
            continue
        if code < 0x20 or code == 0x7F:
            return False
    return True


def _read_text(value: object, label: str, limit: int, narrative: bool, allow_empty: bool) -> str:
    if not isinstance(value, str):
        _fail(f"{label} must be a string")
    if not allow_empty and value == "":
        _fail(f"{label} must be non-empty")
    if utf8_bytes(value) > limit:
        _fail(f"{label} exceeds {limit} UTF-8 bytes")
    if not _controls_ok(value, narrative):
        _fail(f"{label} contains a forbidden character")
    return value


def _read_token(value: object, label: str, pattern: re.Pattern[str]) -> str:
    text = _read_text(value, label, COOPERATION_LIMITS["string_max_bytes"], False, False)
    if pattern.fullmatch(text) is None:
        _fail(f"{label} has an invalid shape")
    return text


def _nullable_token(value: object, label: str, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    return _read_token(value, label, pattern)


def assert_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail(f"{label} must be YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(f"{label} is not a real UTC timestamp")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(f"{label} is not a real UTC timestamp")
    return value


def add_seconds(iso: str, seconds: int) -> str:
    start = assert_timestamp(iso, "timestamp")
    number = _integer(seconds)
    if number is None:
        _fail("seconds must be an integer")
    parsed = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=number)).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_fresh(expires_at: str, now: str) -> bool:
    return assert_timestamp(expires_at, "expires_at") > assert_timestamp(now, "now")


def _https(value: object, label: str) -> str:
    text = _read_text(value, label, COOPERATION_LIMITS["locator_max_bytes"], False, False)
    if not text.startswith("https://"):
        _fail(f"{label} must be a canonical https URL")
    rest = text[len("https://"):]
    if rest == "" or re.search(r"[\s?#@\\]", rest):
        _fail(f"{label} must not carry userinfo, a query, or a fragment")
    host = rest.split("/", 1)[0]
    if _HOST.fullmatch(host) is None or ".." in host:
        _fail(f"{label} host is not canonical")
    return text


def _brief(value: object, label: str) -> str:
    text = _read_text(value, label, COOPERATION_LIMITS["locator_max_bytes"], False, False)
    if _BRIEF.fullmatch(text) is None:
        _fail(f"{label} must be a pinned doc:// reference")
    return text


def _sha(value: object, label: str) -> str:
    text = _read_text(value, label, 64, False, False)
    if _SHA.fullmatch(text) is None:
        _fail(f"{label} must be a full lowercase revision")
    return text


def _record_id(value: object, label: str) -> str:
    text = _read_text(value, label, 32, False, False)
    if _RECORD_ID.fullmatch(text) is None:
        _fail(f"{label} must be a full record id")
    return text


def _digest(value: object, label: str) -> str:
    text = _read_text(value, label, 71, False, False)
    if _DIGEST.fullmatch(text) is None:
        _fail(f"{label} must be a sha256 digest")
    return text


def _exact_keys(obj: dict, required: tuple[str, ...], optional: tuple[str, ...], label: str) -> None:
    allowed = {"type", *required, *optional}
    for key in obj:
        if key not in allowed:
            _fail(f"{label} has {key}")
    for key in required:
        if key not in obj:
            _fail(f"{label}.{key} is required")


def _source(value: object, label: str, evidence: bool) -> dict:
    obj = _object(value, ["type", "provider", "locator", "revision", "id", "digest", "session_id", "start", "end"], label)
    kind = obj.get("type")
    if kind == "repository":
        if evidence:
            _fail(f"{label} is not an evidence source")
        _exact_keys(obj, ("provider", "locator", "revision"), (), label)
        return {"type": kind, "provider": _read_token(obj.get("provider"), f"{label}.provider", _PROVIDER), "locator": _https(obj.get("locator"), f"{label}.locator"), "revision": _sha(obj.get("revision"), f"{label}.revision")}
    if kind == "brief":
        if evidence:
            _fail(f"{label} is not an evidence source")
        _exact_keys(obj, ("locator",), (), label)
        return {"type": kind, "locator": _brief(obj.get("locator"), f"{label}.locator")}
    if kind in ("artifact", "packet"):
        if evidence and kind == "packet":
            _fail(f"{label} is not an evidence source")
        _exact_keys(obj, ("provider", "id", "digest"), (), label)
        return {"type": kind, "provider": _read_token(obj.get("provider"), f"{label}.provider", _PROVIDER), "id": _read_token(obj.get("id"), f"{label}.id", _TOKEN), "digest": _digest(obj.get("digest"), f"{label}.digest")}
    if kind == "session_log":
        _exact_keys(obj, ("provider", "session_id", "start", "end"), ("revision",), label)
        start = _read_int(obj.get("start"), f"{label}.start", 0, SAFE_INT)
        end = _read_int(obj.get("end"), f"{label}.end", start, SAFE_INT)
        revision = obj.get("revision")
        return {
            "type": kind,
            "provider": _read_token(obj.get("provider"), f"{label}.provider", _PROVIDER),
            "session_id": _read_token(obj.get("session_id"), f"{label}.session_id", _TOKEN),
            "start": start,
            "end": end,
            "revision": None if revision is None else _sha(revision, f"{label}.revision"),
        }
    _fail(f"{label} type is not a closed source variant")
    return {}


def _work(value: object) -> dict:
    obj = _object(value, ["repository", "candidate_revision", "url", "workflow", "huddles_task", "huddles_run"], "work_ref")
    workflow = None
    if "workflow" in obj and obj["workflow"] is not None:
        flow = _object(obj["workflow"], ["owner", "ref"], "work_ref.workflow")
        workflow = {"owner": _read_token(flow.get("owner"), "work_ref.workflow.owner", _TOKEN), "ref": _read_token(flow.get("ref"), "work_ref.workflow.ref", _TOKEN)}
    url = obj.get("url")
    return {
        "repository": _https(obj.get("repository"), "work_ref.repository"),
        "candidate_revision": _sha(obj.get("candidate_revision"), "work_ref.candidate_revision"),
        "url": None if url is None else _https(url, "work_ref.url"),
        "workflow": workflow,
        "huddles_task": _nullable_token(obj.get("huddles_task"), "work_ref.huddles_task", _TOKEN) if "huddles_task" in obj else None,
        "huddles_run": _nullable_token(obj.get("huddles_run"), "work_ref.huddles_run", _TOKEN) if "huddles_run" in obj else None,
    }


def _derived(value: object) -> list:
    if not isinstance(value, list):
        _fail("derived_from must be an array")
    if len(value) > COOPERATION_LIMITS["lineage_refs_max"]:
        _fail("too many lineage references")
    items = []
    for index, item in enumerate(value):
        obj = _object(item, ["namespace", "record_id"], f"derived_from[{index}]")
        items.append({
            "namespace": _read_token(obj.get("namespace"), f"derived_from[{index}].namespace", _TOKEN),
            "record_id": _read_token(obj.get("record_id"), f"derived_from[{index}].record_id", _TOKEN),
        })
    items.sort(key=canonical_json)
    for index in range(1, len(items)):
        if canonical_json(items[index - 1]) == canonical_json(items[index]):
            _fail("duplicate derived_from")
    return items


def _provenance(value: object) -> dict:
    obj = _object(value, ["producer_namespace", "original_record_id", "original_run_id", "derived_from"], "provenance")
    run = obj.get("original_run_id")
    return {
        "producer_namespace": _read_token(obj.get("producer_namespace"), "provenance.producer_namespace", _TOKEN),
        "original_record_id": _read_token(obj.get("original_record_id"), "provenance.original_record_id", _TOKEN),
        "original_run_id": None if run is None else _read_token(run, "provenance.original_run_id", _TOKEN),
        "derived_from": [] if "derived_from" not in obj else _derived(obj["derived_from"]),
    }


def _handoff(value: object) -> dict:
    obj = _object(value, ["summary", "attempted", "uncertainty", "suggested_next_step", "source_refs", "evidence_ids"], "payload")
    refs = obj.get("source_refs")
    evidence_ids = obj.get("evidence_ids")
    if not isinstance(refs, list) or len(refs) > COOPERATION_LIMITS["source_refs_max"]:
        _fail("too many source references")
    if not isinstance(evidence_ids, list) or len(evidence_ids) > COOPERATION_LIMITS["lineage_refs_max"]:
        _fail("too many evidence ids")
    ids = [_record_id(item, f"payload.evidence_ids[{index}]") for index, item in enumerate(evidence_ids)]
    if len(set(ids)) != len(ids):
        _fail("duplicate evidence id")
    return {
        "summary": _read_text(obj.get("summary"), "payload.summary", COOPERATION_LIMITS["string_max_bytes"], True, True),
        "attempted": _read_text(obj.get("attempted"), "payload.attempted", COOPERATION_LIMITS["string_max_bytes"], True, True),
        "uncertainty": _read_text(obj.get("uncertainty"), "payload.uncertainty", COOPERATION_LIMITS["string_max_bytes"], True, True),
        "suggested_next_step": _read_text(obj.get("suggested_next_step"), "payload.suggested_next_step", COOPERATION_LIMITS["string_max_bytes"], True, True),
        "source_refs": [_source(item, f"payload.source_refs[{index}]", False) for index, item in enumerate(refs)],
        "evidence_ids": ids,
    }


def _outcome(value: object) -> dict:
    obj = _object(value, ["class", "value", "unit", "label", "reason", "statement"], "payload.outcome")
    kind = obj.get("class")
    if kind == "valid_result":
        if "reason" in obj or "statement" in obj:
            _fail("valid_result cannot carry a failure reason")
        scalar = None
        if "value" in obj and obj["value"] is not None:
            if isinstance(obj["value"], str):
                scalar = _read_text(obj["value"], "payload.outcome.value", COOPERATION_LIMITS["string_max_bytes"], True, True)
            else:
                scalar = _read_int(obj["value"], "payload.outcome.value", -SAFE_INT, SAFE_INT)
        return {
            "class": "valid_result",
            "value": scalar,
            "unit": None if obj.get("unit") is None else _read_text(obj.get("unit"), "payload.outcome.unit", COOPERATION_LIMITS["string_max_bytes"], True, True),
            "label": None if obj.get("label") is None else _read_text(obj.get("label"), "payload.outcome.label", COOPERATION_LIMITS["string_max_bytes"], True, True),
        }
    if any(key in obj for key in ("value", "unit", "label")):
        _fail("only valid_result carries a scalar result")
    if kind == "invalid_candidate":
        if "statement" in obj:
            _fail("unknown field statement")
        return {"class": "invalid_candidate", "reason": _read_text(obj.get("reason"), "payload.outcome.reason", COOPERATION_LIMITS["string_max_bytes"], True, False)}
    if kind == "inconclusive_execution":
        if "statement" in obj:
            _fail("unknown field statement")
        reason = _read_text(obj.get("reason"), "payload.outcome.reason", COOPERATION_LIMITS["string_max_bytes"], False, False)
        if reason not in INCONCLUSIVE_REASONS:
            _fail("inconclusive reason is not a closed value")
        return {"class": "inconclusive_execution", "reason": reason}
    if kind == "assertion":
        if "reason" in obj:
            _fail("unknown field reason")
        return {"class": "assertion", "statement": _read_text(obj.get("statement"), "payload.outcome.statement", COOPERATION_LIMITS["string_max_bytes"], True, False)}
    _fail("outcome class is not one of the four evidence results")
    return {}


def _evidence(value: object) -> dict:
    obj = _object(value, ["requirement_ref", "spec_ref", "candidate_revision", "evaluator_ref", "run_id", "source_ref", "outcome"], "payload")
    requirement = _object(obj.get("requirement_ref"), ["property"], "payload.requirement_ref")
    outcome = _outcome(obj.get("outcome"))
    evaluated = outcome["class"] != "assertion"
    evaluator_ref = None
    if obj.get("evaluator_ref") is not None:
        evaluator = _object(obj["evaluator_ref"], ["identity", "revision"], "payload.evaluator_ref")
        evaluator_ref = {
            "identity": _read_token(evaluator.get("identity"), "payload.evaluator_ref.identity", _TOKEN),
            "revision": _read_token(evaluator.get("revision"), "payload.evaluator_ref.revision", _TOKEN),
        }
    elif evaluated:
        _fail("evaluated evidence requires evaluator_ref")
    run_id = None if obj.get("run_id") is None else _read_token(obj.get("run_id"), "payload.run_id", _TOKEN)
    if evaluated and run_id is None:
        _fail("evaluated evidence requires run_id")
    spec = _read_text(obj.get("spec_ref"), "payload.spec_ref", COOPERATION_LIMITS["locator_max_bytes"], False, False)
    spec_ref = _brief(spec, "payload.spec_ref") if spec.startswith("doc://") else _https(spec, "payload.spec_ref")
    source = _source(obj.get("source_ref"), "payload.source_ref", True)
    if source["type"] not in ("artifact", "session_log"):
        _fail("evidence source must be an artifact or a positioned log")
    return {
        "requirement_ref": {"property": _read_text(requirement.get("property"), "payload.requirement_ref.property", COOPERATION_LIMITS["string_max_bytes"], True, False)},
        "spec_ref": spec_ref,
        "candidate_revision": _sha(obj.get("candidate_revision"), "payload.candidate_revision"),
        "evaluator_ref": evaluator_ref,
        "run_id": run_id,
        "source_ref": source,
        "outcome": outcome,
    }


def _schema_one(value: object) -> bool:
    number = _integer(value)
    return number == 1


def validate_append_context(value: object) -> dict:
    obj = _object(value, ["schema_version", "kind", "project", "idempotency_key", "ttl_seconds", "work_ref", "provenance", "reply_to", "supersedes", "payload"], "append_context")
    if not _schema_one(obj.get("schema_version")):
        _fail("schema_version must be 1")
    if obj.get("kind") not in COOPERATION_KINDS:
        _fail("kind must be handoff or evidence_reference")
    project = _read_token(obj.get("project"), "project", _PROJECT)
    idempotency_key = _read_text(obj.get("idempotency_key"), "idempotency_key", COOPERATION_LIMITS["idempotency_key_max_bytes"], False, False)
    if _TOKEN.fullmatch(idempotency_key) is None:
        _fail("idempotency_key has an invalid shape")
    ttl = COOPERATION_LIMITS["ttl_default_seconds"] if "ttl_seconds" not in obj else _read_int(obj["ttl_seconds"], "ttl_seconds", COOPERATION_LIMITS["ttl_min_seconds"], COOPERATION_LIMITS["ttl_max_seconds"])
    work_ref = _work(obj.get("work_ref"))
    provenance = _provenance(obj.get("provenance"))
    reply_to = None if obj.get("reply_to") is None else _record_id(obj.get("reply_to"), "reply_to")
    supersedes = None if obj.get("supersedes") is None else _record_id(obj.get("supersedes"), "supersedes")
    if reply_to and supersedes and reply_to == supersedes:
        _fail("reply_to and supersedes must be distinct links")
    payload = _handoff(obj.get("payload")) if obj["kind"] == "handoff" else _evidence(obj.get("payload"))
    evidence_ids = payload.get("evidence_ids", [])
    if (reply_to is not None) + (supersedes is not None) + len(evidence_ids) + len(provenance["derived_from"]) > COOPERATION_LIMITS["lineage_refs_max"]:
        _fail("too many lineage references")
    validated = {
        "schema_version": 1,
        "kind": obj["kind"],
        "project": project,
        "idempotency_key": idempotency_key,
        "ttl_seconds": ttl,
        "work_ref": work_ref,
        "provenance": provenance,
        "reply_to": reply_to,
        "supersedes": supersedes,
        "payload": payload,
    }
    if utf8_bytes(canonical_json(validated)) > COOPERATION_LIMITS["record_max_bytes"]:
        _fail("record exceeds 8 KiB")
    return validated


def validate_query_context(value: object) -> dict:
    obj = _object(value, ["schema_version", "project", "query", "work_ref", "kinds", "limit", "max_bytes", "cutoff", "ids", "include_expired"], "query_context")
    if not _schema_one(obj.get("schema_version")):
        _fail("schema_version must be 1")
    project = _read_token(obj.get("project"), "project", _PROJECT)
    max_bytes = COOPERATION_LIMITS["response_default_bytes"] if "max_bytes" not in obj else _read_int(obj["max_bytes"], "max_bytes", COOPERATION_LIMITS["response_min_bytes"], COOPERATION_LIMITS["response_max_bytes"])
    historical = "ids" in obj or "include_expired" in obj
    search_fields = [key for key in ("query", "work_ref", "kinds", "limit", "cutoff") if key in obj]
    if historical and search_fields:
        _fail("historical and search modes cannot be combined")
    if historical:
        ids = obj.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= COOPERATION_LIMITS["exact_ids_max"]:
            _fail("ids must contain 1 to 30 full ids")
        full = [_record_id(item, f"ids[{index}]") for index, item in enumerate(ids)]
        if len(set(full)) != len(full):
            _fail("duplicate id")
        if obj.get("include_expired") is not True:
            _fail("historical reads set include_expired true")
        return {"mode": "historical", "schema_version": 1, "project": project, "ids": full, "include_expired": True, "max_bytes": max_bytes}
    work_ref = None
    if "work_ref" in obj:
        work = _object(obj["work_ref"], ["repository", "candidate_revision"], "work_ref")
        work_ref = {
            "repository": None if "repository" not in work else _https(work["repository"], "work_ref.repository"),
            "candidate_revision": None if "candidate_revision" not in work else _sha(work["candidate_revision"], "work_ref.candidate_revision"),
        }
        if work_ref["repository"] is None and work_ref["candidate_revision"] is None:
            _fail("work_ref needs a repository or a revision")
    kinds = None
    if "kinds" in obj:
        raw = obj["kinds"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
            _fail("kinds must list one or both record kinds")
        if any(item not in COOPERATION_KINDS for item in raw) or len(set(raw)) != len(raw):
            _fail("kinds contains an unknown value" if any(item not in COOPERATION_KINDS for item in raw) else "duplicate kind")
        kinds = list(raw)
    return {
        "mode": "search",
        "schema_version": 1,
        "project": project,
        "query": "" if "query" not in obj else _read_text(obj["query"], "query", COOPERATION_LIMITS["query_text_max_bytes"], True, True),
        "work_ref": work_ref,
        "kinds": kinds,
        "limit": COOPERATION_LIMITS["result_default"] if "limit" not in obj else _read_int(obj["limit"], "limit", COOPERATION_LIMITS["result_min"], COOPERATION_LIMITS["result_max"]),
        "max_bytes": max_bytes,
        "cutoff": None if "cutoff" not in obj else assert_timestamp(obj["cutoff"], "cutoff"),
    }


def causal_key(provenance: dict) -> dict:
    return {
        "producer_namespace": provenance["producer_namespace"],
        "original_record_id": provenance["original_record_id"],
        "original_run_id": provenance["original_run_id"],
    }


def _fingerprint_material(payload: dict) -> dict:
    outcome = payload["outcome"]
    kind = outcome["class"]
    return {
        "candidate_revision": payload["candidate_revision"],
        "evaluator_identity": None if payload["evaluator_ref"] is None else payload["evaluator_ref"]["identity"],
        "evaluator_revision": None if payload["evaluator_ref"] is None else payload["evaluator_ref"]["revision"],
        "outcome_class": kind,
        "outcome_reason": outcome.get("statement") if kind == "assertion" else None if kind == "valid_result" else outcome.get("reason"),
        "requirement_property": payload["requirement_ref"]["property"],
        "result_label": outcome.get("label") if kind == "valid_result" else None,
        "result_unit": outcome.get("unit") if kind == "valid_result" else None,
        "result_value": outcome.get("value") if kind == "valid_result" else None,
        "run_id": payload["run_id"],
        "source_ref": payload["source_ref"],
        "spec_ref": payload["spec_ref"],
    }


def evidence_fingerprint(payload: dict) -> str:
    return sha256_hex(canonical_json(_fingerprint_material(payload)))


def client_payload_digest(value: dict) -> str:
    return sha256_hex(canonical_json(value))


def lineage_relation(left: dict, left_provenance: dict, right: dict, right_provenance: dict) -> str:
    if canonical_json(causal_key(left_provenance)) != canonical_json(causal_key(right_provenance)):
        return "distinct"
    return "duplicate" if evidence_fingerprint(left) == evidence_fingerprint(right) else "conflict"


def _label(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _read_text(value, label, 256, False, False)


def stamp_record(stamp: dict) -> dict:
    principal = stamp.get("principal")
    if principal in (None, ""):
        raise CooperationError("UNAUTHENTICATED", "authenticated principal is required")
    author = _read_text(principal, "principal", 256, False, False)
    origin = _read_token(stamp.get("origin"), "origin", _PROJECT)
    incoming = stamp["input"]
    if origin != incoming["project"]:
        _fail("origin does not match the validated project")
    now = assert_timestamp(stamp.get("now"), "now")
    session = stamp.get("session_id")
    record = {
        "id": _record_id(stamp.get("id"), "id"),
        "schema_version": 1,
        "kind": incoming["kind"],
        "author_principal": author,
        "client_label": _label(stamp.get("client_label"), "client_label"),
        "session_id": None if session is None else _read_token(session, "session_id", _TOKEN),
        "origin": origin,
        "created_at": now,
        "expires_at": add_seconds(now, incoming["ttl_seconds"]),
        "audience": {"kind": "principal", "principal": author},
        "idempotency_key": incoming["idempotency_key"],
        "work_ref": incoming["work_ref"],
        "reply_to": incoming["reply_to"],
        "supersedes": incoming["supersedes"],
        "provenance": {**incoming["provenance"], "reported": True},
        "payload": incoming["payload"],
    }
    if utf8_bytes(canonical_json(record)) > COOPERATION_LIMITS["record_max_bytes"]:
        _fail("stamped record exceeds 8 KiB")
    return record


def record_revision(record: dict) -> str:
    return sha256_hex(canonical_json(record))


def visible_to(record: dict, principal: str, origin: str) -> bool:
    audience = record["audience"]
    return audience["kind"] == "principal" and audience["principal"] == principal and record["author_principal"] == principal and record["origin"] == origin


def hidden_predecessors(records: list, cutoff: str) -> list[str]:
    limit = assert_timestamp(cutoff, "cutoff")
    hidden = set()
    for record in records:
        if record.get("supersedes") and assert_timestamp(record.get("created_at"), "created_at") <= limit:
            hidden.add(record["supersedes"])
    return sorted(hidden)


def _searchable(record: dict) -> str:
    payload = record["payload"]
    if record["kind"] == "handoff":
        return "\n".join([payload["summary"], payload["attempted"], payload["uncertainty"], payload["suggested_next_step"]])
    outcome = payload["outcome"]
    kind = outcome["class"]
    if kind == "assertion":
        tail = outcome["statement"]
    elif kind == "valid_result":
        tail = f"{outcome['label'] or ''}\n{outcome['unit'] or ''}\n{'' if outcome['value'] is None else outcome['value']}"
    else:
        tail = outcome["reason"]
    return "\n".join([payload["requirement_ref"]["property"], payload["spec_ref"], kind, tail])


def compare_selection(left: dict, right: dict, criteria: dict) -> int:
    def rank(record: dict) -> tuple:
        repository = criteria.get("repository")
        revision = criteria.get("candidate_revision")
        if repository is None and revision is None:
            exact = 0
        else:
            repo_ok = repository is None or record["work_ref"]["repository"] == repository
            rev_ok = revision is None or record["work_ref"]["candidate_revision"] == revision
            exact = 0 if repo_ok and rev_ok else 1
        query = criteria.get("query") or ""
        lexical = 0 if query == "" or query in _searchable(record) else 1
        return (exact, lexical, record["created_at"], record["id"])

    a = rank(left)
    b = rank(right)
    if a[0] != b[0]:
        return a[0] - b[0]
    if a[1] != b[1]:
        return a[1] - b[1]
    if a[2] != b[2]:
        return 1 if a[2] < b[2] else -1
    if a[3] != b[3]:
        return -1 if a[3] < b[3] else 1
    return 0


def seal_contribution(draft: dict) -> dict:
    budget = 0
    current = {**draft, "budget_bytes": budget}
    for _ in range(4):
        size = utf8_bytes(canonical_json(current))
        if size == budget:
            return current
        budget = size
        current = {**draft, "budget_bytes": budget}
    _fail("contribution budget did not stabilize")
    return {}


def _assert_record(value: object) -> dict:
    obj = _object(value, ["id", "schema_version", "kind", "author_principal", "client_label", "session_id", "origin", "created_at", "expires_at", "audience", "idempotency_key", "work_ref", "reply_to", "supersedes", "provenance", "payload"], "record")
    if not _schema_one(obj.get("schema_version")):
        _fail("schema_version must be 1")
    audience = _object(obj.get("audience"), ["kind", "principal"], "audience")
    if audience.get("kind") != "principal":
        _fail("audience.kind must be principal")
    provenance = _object(obj.get("provenance"), ["producer_namespace", "original_record_id", "original_run_id", "derived_from", "reported"], "provenance")
    if provenance.get("reported") is not True:
        _fail("provenance.reported must be true")
    created = assert_timestamp(obj.get("created_at"), "created_at")
    expires = assert_timestamp(obj.get("expires_at"), "expires_at")
    span = int((datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")).total_seconds())
    if span < COOPERATION_LIMITS["ttl_min_seconds"] or span > COOPERATION_LIMITS["ttl_max_seconds"]:
        _fail("expiry is outside the TTL bounds")
    if utf8_bytes(canonical_json(obj)) > COOPERATION_LIMITS["record_max_bytes"]:
        _fail("record exceeds 8 KiB")
    if audience.get("principal") != obj.get("author_principal"):
        _fail("audience principal differs from the author")
    return obj


def validate_contribution(value: object) -> dict:
    obj = _object(value, ["schema_version", "as_of", "cutoff", "selector_version", "records", "selected", "max_bytes", "budget_bytes", "omissions", "diagnostics", "window_capped", "candidate_window_exhaustive"], "contribution")
    if not _schema_one(obj.get("schema_version")):
        _fail("schema_version must be 1")
    if obj.get("selector_version") != SELECTOR_VERSION:
        _fail("selector_version is not the v1 selector")
    as_of = assert_timestamp(obj.get("as_of"), "as_of")
    assert_timestamp(obj.get("cutoff"), "cutoff")
    records = obj.get("records")
    if not isinstance(records, list) or len(records) > COOPERATION_LIMITS["result_max"]:
        _fail("too many records")
    checked = [_assert_record(item) for item in records]
    selected = obj.get("selected")
    if not isinstance(selected, list) or len(selected) != len(checked):
        _fail("selected manifest does not match records")
    for index, item in enumerate(selected):
        row = _object(item, ["id", "revision", "fresh"], f"selected[{index}]")
        if row.get("id") != checked[index]["id"]:
            _fail("selected id is out of order")
        if row.get("revision") != record_revision(checked[index]):
            _fail("selected revision does not match the record")
        if row.get("fresh") is not is_fresh(checked[index]["expires_at"], as_of):
            _fail("fresh label does not match expiry")
    max_bytes = _read_int(obj.get("max_bytes"), "max_bytes", COOPERATION_LIMITS["response_min_bytes"], COOPERATION_LIMITS["response_max_bytes"])
    budget = _read_int(obj.get("budget_bytes"), "budget_bytes", 0, COOPERATION_LIMITS["response_max_bytes"])
    if budget > max_bytes:
        _fail("contribution exceeds max_bytes")
    if utf8_bytes(canonical_json(obj)) != budget:
        _fail("budget_bytes is not the canonical contribution size")
    if not isinstance(obj.get("window_capped"), bool) or not isinstance(obj.get("candidate_window_exhaustive"), bool):
        _fail("window flags must be booleans")
    if obj["candidate_window_exhaustive"] is (not obj["window_capped"]):
        pass
    else:
        _fail("a capped window is not exhaustive")
    omissions = obj.get("omissions")
    if not isinstance(omissions, list):
        _fail("omissions must be an array")
    seen = set()
    for omission in omissions:
        item = _object(omission, ["reason", "count"], "omission")
        if item.get("reason") not in ("over_byte_budget", "over_result_limit"):
            _fail("unknown omission reason")
        _read_int(item.get("count"), "omission.count", 0, COOPERATION_LIMITS["population_max"])
        if item["reason"] in seen:
            _fail("duplicate omission reason")
        seen.add(item["reason"])
    diagnostics = obj.get("diagnostics")
    if not isinstance(diagnostics, list) or len(diagnostics) > COOPERATION_LIMITS["diagnostics_max"]:
        _fail("too many diagnostics")
    for diagnostic in diagnostics:
        item = _object(diagnostic, ["code", "causal_key", "record_ids", "fingerprints"], "diagnostic")
        if item.get("code") != "conflicting_reported_evidence":
            _fail("unknown diagnostic")
        _object(item.get("causal_key"), ["producer_namespace", "original_record_id", "original_run_id"], "causal_key")
        if not isinstance(item.get("record_ids"), list) or len(item["record_ids"]) > COOPERATION_LIMITS["diagnostics_max"]:
            _fail("diagnostic record ids are unbounded")
        if not isinstance(item.get("fingerprints"), list) or len(item["fingerprints"]) > COOPERATION_LIMITS["diagnostics_max"]:
            _fail("diagnostic fingerprints are unbounded")
    return obj


_CLOSED = {"type": "object", "additionalProperties": False}
APPEND_CONTEXT_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "description": "Append one client-authored handoff or evidence reference. The server stamps the principal, origin, clock, and expiry. This is not a claim and it takes no authority parameter. Reported results stay reported.",
    "required": ["schema_version", "kind", "project", "idempotency_key", "work_ref", "provenance", "payload"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "kind": {"type": "string", "enum": ["handoff", "evidence_reference"]},
        "project": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "ttl_seconds": {"type": "integer", "minimum": 60, "maximum": 604800},
        "work_ref": dict(_CLOSED),
        "provenance": dict(_CLOSED),
        "reply_to": {"type": "string"},
        "supersedes": {"type": "string"},
        "payload": {**_CLOSED, "description": "Handoff text and source refs, or one evidence reference with exactly one of valid_result, invalid_candidate, inconclusive_execution, or assertion."},
    },
}
QUERY_CONTEXT_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "description": "Return a bounded memory contribution for the authenticated principal and exact origin. Search mode and historical exact-id mode are mutually exclusive. Historical mode sets include_expired true and does not take a query. This takes no authority parameter and does not grant task authority.",
    "required": ["schema_version", "project"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "project": {"type": "string"},
        "query": {"type": "string"},
        "work_ref": dict(_CLOSED),
        "kinds": {"type": "array", "items": {"type": "string", "enum": ["handoff", "evidence_reference"]}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        "max_bytes": {"type": "integer", "minimum": 2048, "maximum": 32768},
        "cutoff": {"type": "string"},
        "ids": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "include_expired": {"type": "boolean"},
    },
}
