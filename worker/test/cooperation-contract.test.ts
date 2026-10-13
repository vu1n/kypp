import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import Ajv from "ajv";
import { handleMcp } from "../src/mcp.ts";
import {
  APPEND_CONTEXT_INPUT_SCHEMA,
  COOPERATION_BROWSE_SQL,
  COOPERATION_EXACT_SQL,
  COOPERATION_FTS_DDL,
  COOPERATION_FTS_MATCH_SQL,
  COOPERATION_LIMITS,
  QUERY_CONTEXT_INPUT_SCHEMA,
  canonicalJson,
  ftsDocument,
  ftsQuery,
  hiddenPredecessors,
  isFresh,
  lineageRelation,
  mergeSearchHits,
  recordRevision,
  sealContribution,
  sha256Hex,
  stampRecord,
  utf8Bytes,
  validateAppendContext,
  validateContribution,
  validateQueryContext,
  visibleTo,
  type CooperationError,
  type CooperationRecord,
} from "../src/cooperation.ts";
import { memoryD1 } from "./d1.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/cooperation/v1.json", import.meta.url), "utf8"));
const byId = new Map(fixture.cases.map((item: { id: string }) => [item.id, item]));

function run(item: { op: string; input: unknown }) {
  return item.op === "append" ? validateAppendContext(item.input) : validateQueryContext(item.input);
}

function rejected(item: { op: string; input: unknown }): CooperationError {
  try {
    run(item);
  } catch (error) {
    return error as CooperationError;
  }
  throw new Error("accepted");
}

const limitMap: Record<string, string> = {
  schemaVersion: "schema_version",
  recordMaxBytes: "record_max_bytes",
  stringMaxBytes: "string_max_bytes",
  locatorMaxBytes: "locator_max_bytes",
  sourceRefsMax: "source_refs_max",
  lineageRefsMax: "lineage_refs_max",
  ttlDefaultSeconds: "ttl_default_seconds",
  ttlMinSeconds: "ttl_min_seconds",
  ttlMaxSeconds: "ttl_max_seconds",
  resultDefault: "result_default",
  resultMin: "result_min",
  resultMax: "result_max",
  queryTextMaxBytes: "query_text_max_bytes",
  responseDefaultBytes: "response_default_bytes",
  responseMinBytes: "response_min_bytes",
  responseMaxBytes: "response_max_bytes",
  candidateWindow: "candidate_window",
  exactIdsMax: "exact_ids_max",
  populationMax: "population_max",
  idempotencyKeyMaxBytes: "idempotency_key_max_bytes",
  diagnosticsMax: "diagnostics_max",
};

test("limits match the frozen fixture", () => {
  for (const [field, wire] of Object.entries(limitMap)) {
    assert.equal(COOPERATION_LIMITS[field as keyof typeof COOPERATION_LIMITS], fixture.limits[wire], wire);
  }
});

for (const vector of fixture.canonical_vectors) {
  test(`canonical ${vector.id}`, () => {
    const encoded = canonicalJson(vector.value);
    assert.equal(encoded, vector.canonical);
    if (vector.sha256) assert.equal(sha256Hex(encoded), vector.sha256);
  });
}

for (const item of fixture.cases) {
  test(item.id, () => {
    if (item.expect === "accept") {
      const validated = run(item);
      if (item.op === "append") {
        assert.equal(sha256Hex(canonicalJson(validated)), item.digest);
      }
      return;
    }
    const error = rejected(item);
    assert.equal(error.code, "INVALID_RECORD");
  });
}

test("identical retry digest ignores the default ttl spelling", () => {
  const digests = fixture.digest_groups.same.map((id: string) => byId.get(id).digest);
  assert.equal(digests[0], digests[1]);
  assert.notEqual(byId.get(fixture.digest_groups.different[0]).digest, byId.get(fixture.digest_groups.different[1]).digest);
});

for (const item of fixture.lineage) {
  test(item.id, () => {
    const left = validateAppendContext(byId.get(item.left).input);
    const right = validateAppendContext(byId.get(item.right).input);
    assert.equal(lineageRelation(left.payload, left.provenance, right.payload, right.provenance), item.relation);
  });
}

for (const item of fixture.expiry) {
  test(item.id, () => {
    assert.equal(isFresh(item.expires_at, item.now), item.fresh);
  });
}

test("stamp is principal-private and expiry is not freshness at the boundary", () => {
  const source = byId.get(fixture.visibility.stamp.case);
  const record = stampRecord({ ...fixture.visibility.stamp, input: validateAppendContext(source.input) });
  assert.equal(record.expires_at, fixture.visibility.expires_at);
  assert.equal(record.provenance.reported, true);
  assert.equal(Object.hasOwn(record, "authority"), false);
  assert.equal(utf8Bytes(canonicalJson(record)) <= COOPERATION_LIMITS.recordMaxBytes, true);
  for (const check of fixture.visibility.checks) {
    assert.equal(visibleTo(record, check.principal, check.origin), check.visible, `${check.principal}/${check.origin}`);
  }
});

test("supersession hides only links that existed by the cutoff", () => {
  assert.deepEqual(hiddenPredecessors(fixture.hidden_predecessors.records, fixture.hidden_predecessors.cutoff), fixture.hidden_predecessors.hidden);
});

test("selection order is exact work, then bm25, then id", () => {
  const criteria = fixture.selection.criteria;
  const ranked = referenceSearch(fixture.selection.records.map((item: { record: CooperationRecord }) => item.record), {
    principal: "owner",
    origin: "kypp",
    now: "2026-10-13T12:00:00Z",
    cutoff: "2026-10-13T12:00:00Z",
    query: criteria.query,
    repository: criteria.repository,
    candidate_revision: criteria.candidate_revision,
  });
  const names = new Map(fixture.selection.records.map((item: { id: string; record: CooperationRecord }) => [item.record.id, item.id]));
  assert.deepEqual(ranked.ids.map((id) => names.get(id)), fixture.selection.order);
  assert.equal(ranked.ids.includes("3123456789abcdef0123456789abcdef"), false);
});

test("empty contribution budget counts its own canonical bytes", () => {
  const sealed = sealContribution(fixture.contribution.draft);
  assert.equal(sha256Hex(canonicalJson(sealed)), fixture.contribution.sha256);
  assert.equal(sealed.budget_bytes, utf8Bytes(canonicalJson(sealed)));
  assert.equal(validateContribution(sealed).budget_bytes, sealed.budget_bytes);
});

function propertyNames(schema: unknown): string[] {
  const names: string[] = [];
  const visit = (node: unknown) => {
    if (!node || typeof node !== "object") return;
    const record = node as Record<string, unknown>;
    if (record.properties && typeof record.properties === "object") {
      for (const [key, value] of Object.entries(record.properties as Record<string, unknown>)) {
        names.push(key);
        visit(value);
      }
    }
    for (const key of ["items", "then", "else"]) visit(record[key]);
    for (const key of ["oneOf", "anyOf", "allOf"]) {
      if (Array.isArray(record[key])) for (const item of record[key] as unknown[]) visit(item);
    }
  };
  visit(schema);
  return names;
}

function referenceSearch(records: CooperationRecord[], request: {
  principal: string;
  origin: string;
  now: string;
  cutoff: string;
  query: string;
  repository: string | null;
  candidate_revision: string | null;
  kinds?: string[] | null;
  window?: number;
}) {
  const db = new DatabaseSync(":memory:");
  db.exec("CREATE TABLE cooperation_records (id TEXT PRIMARY KEY, author_principal TEXT NOT NULL, origin TEXT NOT NULL, audience_principal TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, supersedes TEXT, repository TEXT NOT NULL, candidate_revision TEXT NOT NULL)");
  db.exec(COOPERATION_FTS_DDL);
  const insertRecord = db.prepare("INSERT INTO cooperation_records(id, author_principal, origin, audience_principal, kind, created_at, expires_at, supersedes, repository, candidate_revision) VALUES (?,?,?,?,?,?,?,?,?,?)");
  const insertFts = db.prepare("INSERT INTO cooperation_fts(record_id, repository, candidate_revision, body) VALUES (?,?,?,?)");
  for (const record of records) {
    const document = ftsDocument(record);
    insertRecord.run(record.id, record.author_principal, record.origin, record.audience.principal, record.kind, record.created_at, record.expires_at, record.supersedes, document.repository, document.candidate_revision);
    insertFts.run(document.record_id, document.repository, document.candidate_revision, document.body);
  }
  const kinds = request.kinds ?? null;
  const filter = {
    principal: request.principal,
    origin: request.origin,
    cutoff: request.cutoff,
    now: request.now,
    kind: !kinds || kinds.length === 2 ? null : kinds[0],
  };
  const match = ftsQuery(request.query);
  const window = request.window ?? COOPERATION_LIMITS.candidateWindow;
  if (match === "") {
    const rows = db.prepare(`${COOPERATION_BROWSE_SQL}\nLIMIT :limit`).all({
      ...filter,
      repository: request.repository,
      revision: request.candidate_revision,
      limit: window + 1,
    }) as { id: string }[];
    return { ids: rows.slice(0, window).map((row) => row.id), window_capped: rows.length > window };
  }
  const ftsHits = db.prepare(COOPERATION_FTS_MATCH_SQL).all({ ...filter, match }) as { id: string; score: number }[];
  const exact = db.prepare(COOPERATION_EXACT_SQL).all({
    ...filter,
    repository: request.repository,
    revision: request.candidate_revision,
  }) as { id: string }[];
  return mergeSearchHits(exact.map((row) => row.id), ftsHits, window);
}

test("append and query schemas take no authority parameter", () => {
  assert.equal(APPEND_CONTEXT_INPUT_SCHEMA.additionalProperties, false);
  assert.equal(QUERY_CONTEXT_INPUT_SCHEMA.additionalProperties, false);
  assert.deepEqual(Object.keys(APPEND_CONTEXT_INPUT_SCHEMA.properties).sort(), [...fixture.append_properties].sort());
  assert.deepEqual(Object.keys(QUERY_CONTEXT_INPUT_SCHEMA.properties).sort(), [...fixture.query_properties].sort());
  const names = [...propertyNames(APPEND_CONTEXT_INPUT_SCHEMA), ...propertyNames(QUERY_CONTEXT_INPUT_SCHEMA)];
  for (const forbidden of fixture.forbidden_properties) assert.equal(names.includes(forbidden), false, forbidden);
  for (const field of ["authority", "verify", "measured", "capability", "approval"]) {
    const error = rejected({ op: "append", input: { ...byId.get("empty-handoff").input, [field]: "x" } });
    assert.equal(error.code, "INVALID_RECORD");
    assert.match(error.message, new RegExp(field));
  }
});

test("nonfinite and fractional numbers fail closed", () => {
  for (const limit of [Number.NaN, Number.POSITIVE_INFINITY, 1.5]) {
    const error = rejected({ op: "query", input: { schema_version: 1, project: "kypp", limit } });
    assert.equal(error.code, "INVALID_RECORD");
  }
});

test("fts query quotes tokens and drops match syntax", () => {
  assert.equal(ftsQuery(""), "");
  assert.equal(ftsQuery("*"), "");
  assert.equal(ftsQuery("\""), "");
  assert.equal(ftsQuery("lexical-hit"), "\"lexical\" OR \"hit\"");
  assert.equal(ftsQuery("NOT alpha"), "\"NOT\" OR \"alpha\"");
  assert.equal(ftsQuery("body:secret"), "\"body\" OR \"secret\"");
  assert.equal(ftsQuery("NEAR(alpha beta)"), "\"NEAR\" OR \"alpha\" OR \"beta\"");
  assert.equal(ftsQuery("café"), "\"café\"");
});

test("an invisible better match never surfaces or consumes a slot", () => {
  const visible = structuredClone(fixture.selection.records[2].record) as CooperationRecord;
  const hidden = structuredClone(visible) as CooperationRecord;
  hidden.id = "4123456789abcdef0123456789abcdef";
  hidden.author_principal = "other";
  hidden.audience = { kind: "principal", principal: "other" };
  hidden.payload = { ...hidden.payload, summary: "lexical-hit lexical-hit lexical-hit lexical-hit" } as CooperationRecord["payload"];
  const ranked = referenceSearch([visible, hidden], {
    principal: "owner",
    origin: "kypp",
    now: "2026-10-13T12:00:00Z",
    cutoff: "2026-10-13T12:00:00Z",
    query: "lexical-hit",
    repository: null,
    candidate_revision: null,
    window: 1,
  });
  assert.deepEqual(ranked.ids, [visible.id]);
  assert.equal(ranked.window_capped, false);
});

test("an older relevant record is findable past a hundred newer misses", () => {
  const old = structuredClone(fixture.selection.records[0].record) as CooperationRecord;
  old.id = "5123456789abcdef0123456789abcdef";
  old.created_at = "2020-01-01T00:00:00Z";
  old.expires_at = "2030-01-01T00:00:00Z";
  old.work_ref = { ...old.work_ref, repository: "https://github.com/vu1n/other", candidate_revision: "cccccccccccccccccccccccccccccccccccccccc" };
  old.payload = { ...old.payload, summary: "uniquealpha uniquealpha uniquealpha" } as CooperationRecord["payload"];
  const newer = Array.from({ length: 100 }, (_, index) => {
    const row = structuredClone(fixture.selection.records[3].record) as CooperationRecord;
    row.id = index.toString(16).padStart(32, "0");
    row.created_at = "2026-10-13T05:00:00Z";
    row.payload = { ...row.payload, summary: `noise${index}` } as CooperationRecord["payload"];
    return row;
  });
  const ranked = referenceSearch([old, ...newer], {
    principal: "owner",
    origin: "kypp",
    now: "2026-10-13T12:00:00Z",
    cutoff: "2026-10-13T12:00:00Z",
    query: "uniquealpha",
    repository: null,
    candidate_revision: null,
  });
  assert.deepEqual(ranked.ids, [old.id]);
});

test("hostile match syntax cannot widen the authorized set", () => {
  const rows = fixture.selection.records.map((item: { record: CooperationRecord }) => item.record);
  const hidden = structuredClone(rows[0]) as CooperationRecord;
  hidden.id = "6123456789abcdef0123456789abcdef";
  hidden.author_principal = "other";
  hidden.audience = { kind: "principal", principal: "other" };
  hidden.payload = { ...hidden.payload, summary: "secret" } as CooperationRecord["payload"];
  for (const query of ["*", "\"", "NOT secret", "body:secret", "NEAR(secret *)"]) {
    const ranked = referenceSearch([...rows, hidden], {
      principal: "owner",
      origin: "kypp",
      now: "2026-10-13T12:00:00Z",
      cutoff: "2026-10-13T12:00:00Z",
      query,
      repository: null,
      candidate_revision: null,
    });
    assert.equal(ranked.ids.includes(hidden.id), false, query);
  }
  const notOperator = referenceSearch(rows, {
    principal: "owner",
    origin: "kypp",
    now: "2026-10-13T12:00:00Z",
    cutoff: "2026-10-13T12:00:00Z",
    query: "plain NOT lexical-hit",
    repository: null,
    candidate_revision: null,
  });
  const names = new Map(fixture.selection.records.map((item: { id: string; record: CooperationRecord }) => [item.record.id, item.id]));
  assert.deepEqual(notOperator.ids.map((id) => names.get(id)).sort(), ["s-exact-lexical", "s-exact-plain", "s-lexical", "s-neither"].sort());
});

test("equal bm25 scores break ties by id", () => {
  const left = structuredClone(fixture.selection.records[3].record) as CooperationRecord;
  const right = structuredClone(left) as CooperationRecord;
  left.id = "a123456789abcdef0123456789abcdef";
  right.id = "b123456789abcdef0123456789abcdef";
  left.payload = { ...left.payload, summary: "sameword" } as CooperationRecord["payload"];
  right.payload = { ...right.payload, summary: "sameword" } as CooperationRecord["payload"];
  const ranked = referenceSearch([right, left], {
    principal: "owner",
    origin: "kypp",
    now: "2026-10-13T12:00:00Z",
    cutoff: "2026-10-13T12:00:00Z",
    query: "SAMEWORD",
    repository: null,
    candidate_revision: null,
  });
  assert.deepEqual(ranked.ids, [left.id, right.id]);
});

test("json schema accepts fixture inputs and rejects closed nested objects", () => {
  const ajv = new Ajv({ strict: false, allErrors: true });
  const append = ajv.compile(APPEND_CONTEXT_INPUT_SCHEMA);
  const query = ajv.compile(QUERY_CONTEXT_INPUT_SCHEMA);
  for (const item of fixture.cases) {
    const validate = item.op === "append" ? append : query;
    const ok = Boolean(validate(item.input));
    if (item.expect === "accept") assert.equal(ok, true, `${item.id} ${ajv.errorsText(validate.errors)}`);
    else {
      let runtimeOk = true;
      try {
        run(item);
        runtimeOk = true;
      } catch {
        runtimeOk = false;
      }
      assert.equal(runtimeOk, false, item.id);
      assert.equal(ok && runtimeOk, false, item.id);
    }
  }
  const nested = structuredClone(byId.get("empty-handoff").input);
  nested.work_ref.note = "extra";
  assert.equal(append(nested), false);
  const payload = structuredClone(byId.get("empty-handoff").input);
  payload.payload.authority = "human";
  assert.equal(append(payload), false);
  const queryWork = { schema_version: 1, project: "kypp", work_ref: { repository: "https://github.com/vu1n/kypp", note: "x" } };
  assert.equal(query(queryWork), false);
});

test("stamped contribution records reject a bad id and a bad kind", () => {
  const source = byId.get(fixture.visibility.stamp.case);
  const record = stampRecord({ ...fixture.visibility.stamp, input: validateAppendContext(source.input) });
  const draft = {
    schema_version: 1,
    as_of: record.created_at,
    cutoff: record.created_at,
    selector_version: "cooperation-fts5-bm25-v1",
    records: [record],
    selected: [{ id: record.id, revision: recordRevision(record), fresh: isFresh(record.expires_at, record.created_at) }],
    max_bytes: 32768,
    omissions: [],
    diagnostics: [],
    window_capped: false,
    candidate_window_exhaustive: true,
  };
  const sealed = sealContribution(draft);
  assert.equal(validateContribution(sealed).records[0].id, record.id);
  const badId = structuredClone(sealed);
  badId.records[0].id = "NOT-AN-ID";
  assert.throws(() => validateContribution(badId), (error: CooperationError) => error.code === "INVALID_RECORD" && /id/.test(error.message));
  const badKind = structuredClone(sealed);
  badKind.records[0].kind = "root";
  assert.throws(() => validateContribution(badKind), (error: CooperationError) => error.code === "INVALID_RECORD" && /kind/.test(error.message));
});

test("hosted MCP tools are still the claim surface", async () => {
  const db = memoryD1();
  const response = await handleMcp(new Request("https://k/mcp", {
    method: "POST",
    headers: { "X-Kypp-Project": "kypp" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
  }), db, { user: "owner", agent: "contract" });
  const body = await response.json() as { result?: { tools?: { name: string; inputSchema?: { properties?: Record<string, unknown> } }[] } };
  const tools = body.result?.tools ?? [];
  assert.deepEqual(tools.map((tool) => tool.name), ["briefing", "recall", "claim", "expand", "correct"]);
  for (const tool of tools) {
    const props = tool.inputSchema?.properties ?? {};
    assert.equal(Object.hasOwn(props, "verify"), false, tool.name);
    assert.equal(Object.hasOwn(props, "authority"), false, tool.name);
  }
});

test("the contract module does not register an MCP tool list", () => {
  const source = readFileSync(new URL("../src/cooperation.ts", import.meta.url), "utf8");
  assert.equal(source.includes("const TOOLS"), false);
});
