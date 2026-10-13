import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { handleMcp } from "../src/mcp.ts";
import {
  APPEND_CONTEXT_INPUT_SCHEMA,
  COOPERATION_LIMITS,
  QUERY_CONTEXT_INPUT_SCHEMA,
  canonicalJson,
  compareSelection,
  hiddenPredecessors,
  isFresh,
  lineageRelation,
  sealContribution,
  sha256Hex,
  stampRecord,
  utf8Bytes,
  validateAppendContext,
  validateContribution,
  validateQueryContext,
  visibleTo,
  type CooperationError,
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

test("selection order is exact work, then lexical, then newer, then id", () => {
  const ranked = [...fixture.selection.records].sort((left, right) => compareSelection(left.record, right.record, fixture.selection.criteria));
  assert.deepEqual(ranked.map((item) => item.id), fixture.selection.order);
});

test("empty contribution budget counts its own canonical bytes", () => {
  const sealed = sealContribution(fixture.contribution.draft);
  assert.equal(sha256Hex(canonicalJson(sealed)), fixture.contribution.sha256);
  assert.equal(sealed.budget_bytes, utf8Bytes(canonicalJson(sealed)));
  assert.equal(validateContribution(sealed).budget_bytes, sealed.budget_bytes);
});

function propertyNames(schema: { properties?: Record<string, unknown>; items?: unknown }): string[] {
  const names: string[] = [];
  if (schema.properties) {
    for (const [key, value] of Object.entries(schema.properties)) {
      names.push(key);
      names.push(...propertyNames(value as { properties?: Record<string, unknown>; items?: unknown }));
    }
  }
  if (schema.items) names.push(...propertyNames(schema.items as { properties?: Record<string, unknown> }));
  return names;
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
