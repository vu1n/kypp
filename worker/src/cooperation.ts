// Context: doc://kypp/memory-scope-decay@0005#scope-keys-decay — cooperation records are not claims; wall-clock expiry never deletes, promotes, or corroborates them.
// Context: doc://kypp/agent-mcp-surface-compose@0002#compose-surface — append_context and query_context take no authority parameter and return a bounded memory contribution.
// Frozen v1 contract shared with kypp/cooperation.py. No storage, no MCP registration, no model call.

export const SCHEMA_VERSION = 1 as const;
export const SELECTOR_VERSION = "cooperation-lexical-v1";

export const COOPERATION_LIMITS = Object.freeze({
  schemaVersion: SCHEMA_VERSION,
  recordMaxBytes: 8192,
  stringMaxBytes: 1024,
  locatorMaxBytes: 512,
  sourceRefsMax: 16,
  lineageRefsMax: 16,
  ttlDefaultSeconds: 86_400,
  ttlMinSeconds: 60,
  ttlMaxSeconds: 604_800,
  resultDefault: 12,
  resultMin: 1,
  resultMax: 30,
  queryTextMaxBytes: 512,
  responseDefaultBytes: 16_384,
  responseMinBytes: 2048,
  responseMaxBytes: 32_768,
  candidateWindow: 100,
  exactIdsMax: 30,
  populationMax: 10_000,
  idempotencyKeyMaxBytes: 128,
  diagnosticsMax: 16,
});

export const COOPERATION_ERROR_CODES = [
  "INVALID_RECORD",
  "UNKNOWN_ORIGIN",
  "UNAUTHENTICATED",
  "IDEMPOTENCY_CONFLICT",
  "CAPACITY_EXCEEDED",
  "UNAVAILABLE",
] as const;

export type CooperationErrorCode = (typeof COOPERATION_ERROR_CODES)[number];

export class CooperationError extends Error {
  readonly code: CooperationErrorCode;
  constructor(code: CooperationErrorCode, message: string) {
    super(message);
    this.name = "CooperationError";
    this.code = code;
  }
}

export const COOPERATION_KINDS = ["handoff", "evidence_reference"] as const;
export type CooperationKind = (typeof COOPERATION_KINDS)[number];

export const SOURCE_TYPES = ["repository", "brief", "artifact", "session_log", "packet"] as const;
export const EVIDENCE_SOURCE_TYPES = ["artifact", "session_log"] as const;
export const OUTCOME_CLASSES = ["valid_result", "invalid_candidate", "inconclusive_execution", "assertion"] as const;
export const INCONCLUSIVE_REASONS = ["timeout", "spawn_failure", "evaluator_failure", "infrastructure", "legacy_false"] as const;
export type InconclusiveReason = (typeof INCONCLUSIVE_REASONS)[number];

export const FORBIDDEN_FIELDS = [
  "access_url",
  "approval",
  "assignee",
  "authority",
  "bearer",
  "capability",
  "command",
  "credential",
  "grant",
  "measured",
  "password",
  "room",
  "secret",
  "signed_url",
  "status",
  "token",
  "transcript",
  "verifier",
  "verify",
] as const;

const FORBIDDEN = new Set<string>(FORBIDDEN_FIELDS);
const SHA40_OR_64 = /^[0-9a-f]{40}$|^[0-9a-f]{64}$/;
const RECORD_ID = /^[0-9a-f]{32}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const PROVIDER = /^[a-z][a-z0-9_-]{0,31}$/;
const PROJECT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const TOKEN = /^[A-Za-z0-9._:-]{1,128}$/;
const BRIEF_REF = /^doc:\/\/[A-Za-z0-9][A-Za-z0-9_-]*\/[A-Za-z0-9][A-Za-z0-9_-]*@[0-9]+#[A-Za-z0-9][A-Za-z0-9_-]*$/;
const TIMESTAMP = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
const SAFE_INT = 9_007_199_254_740_991;

export interface DerivedRef {
  namespace: string;
  record_id: string;
}

export interface Provenance {
  producer_namespace: string;
  original_record_id: string;
  original_run_id: string | null;
  derived_from: DerivedRef[];
}

export interface StoredProvenance extends Provenance {
  reported: true;
}

export type SourceRef =
  | { type: "repository"; provider: string; locator: string; revision: string }
  | { type: "brief"; locator: string }
  | { type: "artifact"; provider: string; id: string; digest: string }
  | { type: "session_log"; provider: string; session_id: string; start: number; end: number; revision: string | null }
  | { type: "packet"; provider: string; id: string; digest: string };

export interface WorkRef {
  repository: string;
  candidate_revision: string;
  url: string | null;
  workflow: { owner: string; ref: string } | null;
  huddles_task: string | null;
  huddles_run: string | null;
}

export interface HandoffPayload {
  summary: string;
  attempted: string;
  uncertainty: string;
  suggested_next_step: string;
  source_refs: SourceRef[];
  evidence_ids: string[];
}

export type Outcome =
  | { class: "valid_result"; value: number | string | null; unit: string | null; label: string | null }
  | { class: "invalid_candidate"; reason: string }
  | { class: "inconclusive_execution"; reason: InconclusiveReason }
  | { class: "assertion"; statement: string };

export interface EvidencePayload {
  requirement_ref: { property: string };
  spec_ref: string;
  candidate_revision: string;
  evaluator_ref: { identity: string; revision: string } | null;
  run_id: string | null;
  source_ref: Extract<SourceRef, { type: "artifact" | "session_log" }>;
  outcome: Outcome;
}

export interface ValidatedAppend {
  schema_version: 1;
  kind: CooperationKind;
  project: string;
  idempotency_key: string;
  ttl_seconds: number;
  work_ref: WorkRef;
  provenance: Provenance;
  reply_to: string | null;
  supersedes: string | null;
  payload: HandoffPayload | EvidencePayload;
}

export interface CooperationRecord {
  id: string;
  schema_version: 1;
  kind: CooperationKind;
  author_principal: string;
  client_label: string | null;
  session_id: string | null;
  origin: string;
  created_at: string;
  expires_at: string;
  audience: { kind: "principal"; principal: string };
  idempotency_key: string;
  work_ref: WorkRef;
  reply_to: string | null;
  supersedes: string | null;
  provenance: StoredProvenance;
  payload: HandoffPayload | EvidencePayload;
}

export type ValidatedQuery =
  | {
      mode: "search";
      schema_version: 1;
      project: string;
      query: string;
      work_ref: { repository: string | null; candidate_revision: string | null } | null;
      kinds: CooperationKind[] | null;
      limit: number;
      max_bytes: number;
      cutoff: string | null;
    }
  | {
      mode: "historical";
      schema_version: 1;
      project: string;
      ids: string[];
      include_expired: true;
      max_bytes: number;
    };

export interface AppendReceipt {
  schema_version: 1;
  id: string;
  created_at: string;
  expires_at: string;
  duplicate: boolean;
}

export interface ConflictingEvidenceDiagnostic {
  code: "conflicting_reported_evidence";
  causal_key: { producer_namespace: string; original_record_id: string; original_run_id: string | null };
  record_ids: string[];
  fingerprints: string[];
}

export interface ContextContribution {
  schema_version: 1;
  as_of: string;
  cutoff: string;
  selector_version: typeof SELECTOR_VERSION;
  records: CooperationRecord[];
  selected: { id: string; revision: string; fresh: boolean }[];
  max_bytes: number;
  budget_bytes: number;
  omissions: { reason: "over_byte_budget" | "over_result_limit"; count: number }[];
  diagnostics: ConflictingEvidenceDiagnostic[];
  window_capped: boolean;
  candidate_window_exhaustive: boolean;
}

export interface AppendRequest {
  principal: string;
  origin: string;
  now: string;
  client_label: string | null;
  session_id: string | null;
  input: ValidatedAppend;
}

export interface ExactReadRequest {
  principal: string;
  origin: string;
  now: string;
  ids: readonly string[];
  include_expired: true;
}

export interface ScanRequest {
  principal: string;
  origin: string;
  now: string;
  cutoff: string;
  kinds: readonly CooperationKind[] | null;
  window: number;
}

export interface CandidateWindow {
  records: CooperationRecord[];
  window_capped: boolean;
}

// PC-003 implements this. scanCandidates returns only rows already limited to the
// authenticated principal and exact origin, with created_at <= cutoff and
// expires_at > now. It also omits predecessors hidden by a supersession link that
// existed at cutoff, including when the superseder itself is expired. getByIds
// uses the same principal and origin boundary, keeps expired rows because
// include_expired is required, and does not hide superseded rows. Neither method
// ranks, calls a model, or returns another principal's ids. append resolves an
// identical idempotency key before the population cap; a changed payload with
// that key is IDEMPOTENCY_CONFLICT and does not evict history.
export interface CooperationRepository {
  append(request: AppendRequest): Promise<AppendReceipt>;
  getByIds(request: ExactReadRequest): Promise<CooperationRecord[]>;
  scanCandidates(request: ScanRequest): Promise<CandidateWindow>;
}

export function utf8Bytes(value: string): number {
  return new TextEncoder().encode(value).length;
}

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotr(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits));
}

export function sha256Hex(value: string): string {
  const bytes = new TextEncoder().encode(value);
  const padded = new Uint8Array((bytes.length + 9 + 63) & ~63);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  const bits = bytes.length * 8;
  view.setUint32(padded.length - 8, Math.floor(bits / 0x100000000));
  view.setUint32(padded.length - 4, bits >>> 0);
  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
  let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  const w = new Uint32Array(64);
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let t = 0; t < 16; t += 1) w[t] = view.getUint32(offset + t * 4);
    for (let t = 16; t < 64; t += 1) {
      const s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
      const s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
      w[t] = (w[t - 16] + s0 + w[t - 7] + s1) >>> 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let t = 0; t < 64; t += 1) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + ch + SHA256_K[t] + w[t]) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
  }
  return [h0, h1, h2, h3, h4, h5, h6, h7].map((part) => part.toString(16).padStart(8, "0")).join("");
}

export function canonicalJson(value: unknown): string {
  return encodeCanonical(value);
}

function encodeCanonical(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new CooperationError("INVALID_RECORD", "canonical JSON only accepts safe integers");
    return String(value === 0 ? 0 : value);
  }
  if (typeof value === "string") return encodeString(value);
  if (Array.isArray(value)) return `[${value.map((item) => encodeCanonical(item)).join(",")}]`;
  if (isObject(value)) {
    const keys = Object.keys(value).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${keys.map((key) => `${encodeString(key)}:${encodeCanonical(value[key])}`).join(",")}}`;
  }
  throw new CooperationError("INVALID_RECORD", "value cannot be canonicalized");
}

function encodeString(value: string): string {
  let out = '"';
  for (const char of value) {
    const code = char.codePointAt(0)!;
    if (code >= 0xd800 && code <= 0xdfff) throw new CooperationError("INVALID_RECORD", "unpaired surrogate");
    if (char === '"') out += '\\"';
    else if (char === "\\") out += "\\\\";
    else if (char === "\b") out += "\\b";
    else if (char === "\f") out += "\\f";
    else if (char === "\n") out += "\\n";
    else if (char === "\r") out += "\\r";
    else if (char === "\t") out += "\\t";
    else if (code < 0x20) out += `\\u${code.toString(16).padStart(4, "0")}`;
    else out += char;
  }
  return `${out}"`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fail(message: string): never {
  throw new CooperationError("INVALID_RECORD", message);
}

function readObject(value: unknown, allowed: readonly string[], label: string): Record<string, unknown> {
  if (!isObject(value)) fail(`${label} must be an object`);
  for (const key of Object.keys(value)) {
    if (FORBIDDEN.has(key)) fail(`forbidden field ${key}`);
    if (!allowed.includes(key)) fail(`unknown field ${key}`);
  }
  return value;
}

function readInt(value: unknown, label: string, min: number, max: number): number {
  if (typeof value === "boolean" || typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    fail(`${label} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function optionalInt(value: unknown, label: string, min: number, max: number, fallback: number): number {
  if (value === undefined) return fallback;
  return readInt(value, label, min, max);
}

function controlsOk(value: string, narrative: boolean): boolean {
  for (const char of value) {
    const code = char.codePointAt(0)!;
    if (code === 0x09 || code === 0x0a) {
      if (!narrative) return false;
      continue;
    }
    if (code < 0x20 || code === 0x7f) return false;
  }
  return true;
}

function readText(value: unknown, label: string, max: number, narrative: boolean, allowEmpty: boolean): string {
  if (typeof value !== "string") fail(`${label} must be a string`);
  if (!allowEmpty && value.length === 0) fail(`${label} must be non-empty`);
  if (utf8Bytes(value) > max) fail(`${label} exceeds ${max} UTF-8 bytes`);
  if (!controlsOk(value, narrative)) fail(`${label} contains a forbidden character`);
  return value;
}

function readToken(value: unknown, label: string, pattern: RegExp): string {
  const text = readText(value, label, COOPERATION_LIMITS.stringMaxBytes, false, false);
  if (!pattern.test(text)) fail(`${label} has an invalid shape`);
  return text;
}

function nullableToken(value: unknown, label: string, pattern: RegExp): string | null {
  if (value === undefined || value === null) return null;
  return readToken(value, label, pattern);
}

function assertTimestamp(value: unknown, label: string): string {
  if (typeof value !== "string" || !TIMESTAMP.test(value)) fail(`${label} must be YYYY-MM-DDTHH:MM:SSZ`);
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) fail(`${label} is not a real UTC timestamp`);
  const back = new Date(parsed).toISOString().replace(/\.\d{3}Z$/, "Z");
  if (back !== value) fail(`${label} is not a real UTC timestamp`);
  return value;
}

export function addSeconds(iso: string, seconds: number): string {
  const start = assertTimestamp(iso, "timestamp");
  if (!Number.isSafeInteger(seconds)) fail("seconds must be an integer");
  return new Date(Date.parse(start) + seconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function isFresh(expiresAt: string, now: string): boolean {
  return assertTimestamp(expiresAt, "expires_at") > assertTimestamp(now, "now");
}

function assertHttps(value: unknown, label: string): string {
  const text = readText(value, label, COOPERATION_LIMITS.locatorMaxBytes, false, false);
  if (!text.startsWith("https://")) fail(`${label} must be a canonical https URL`);
  const rest = text.slice("https://".length);
  if (rest.length === 0 || /[\s?#@\\]/.test(rest)) fail(`${label} must not carry userinfo, a query, or a fragment`);
  const slash = rest.indexOf("/");
  const host = slash === -1 ? rest : rest.slice(0, slash);
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?$/.test(host) || host.includes("..")) {
    fail(`${label} host is not canonical`);
  }
  return text;
}

function assertBrief(value: unknown, label: string): string {
  const text = readText(value, label, COOPERATION_LIMITS.locatorMaxBytes, false, false);
  if (!BRIEF_REF.test(text)) fail(`${label} must be a pinned doc:// reference`);
  return text;
}

function assertSha(value: unknown, label: string): string {
  const text = readText(value, label, 64, false, false);
  if (!SHA40_OR_64.test(text)) fail(`${label} must be a full lowercase revision`);
  return text;
}

function assertRecordId(value: unknown, label: string): string {
  const text = readText(value, label, 32, false, false);
  if (!RECORD_ID.test(text)) fail(`${label} must be a full record id`);
  return text;
}

function assertDigest(value: unknown, label: string): string {
  const text = readText(value, label, 71, false, false);
  if (!DIGEST.test(text)) fail(`${label} must be a sha256 digest`);
  return text;
}

function assertProvider(value: unknown, label: string): string {
  return readToken(value, label, PROVIDER);
}

function exactKeys(obj: Record<string, unknown>, required: readonly string[], optional: readonly string[], label: string): void {
  const allowed = new Set(["type", ...required, ...optional]);
  for (const key of Object.keys(obj)) {
    if (!allowed.has(key)) fail(`${label} has ${key}`);
  }
  for (const key of required) {
    if (obj[key] === undefined) fail(`${label}.${key} is required`);
  }
}

function readSource(value: unknown, label: string, evidence: boolean): SourceRef {
  const obj = readObject(value, ["type", "provider", "locator", "revision", "id", "digest", "session_id", "start", "end"], label);
  const type = obj.type;
  if (type === "repository") {
    if (evidence) fail(`${label} is not an evidence source`);
    exactKeys(obj, ["provider", "locator", "revision"], [], label);
    return { type, provider: assertProvider(obj.provider, `${label}.provider`), locator: assertHttps(obj.locator, `${label}.locator`), revision: assertSha(obj.revision, `${label}.revision`) };
  }
  if (type === "brief") {
    if (evidence) fail(`${label} is not an evidence source`);
    exactKeys(obj, ["locator"], [], label);
    return { type, locator: assertBrief(obj.locator, `${label}.locator`) };
  }
  if (type === "artifact" || type === "packet") {
    if (evidence && type === "packet") fail(`${label} is not an evidence source`);
    exactKeys(obj, ["provider", "id", "digest"], [], label);
    return { type, provider: assertProvider(obj.provider, `${label}.provider`), id: readToken(obj.id, `${label}.id`, TOKEN), digest: assertDigest(obj.digest, `${label}.digest`) };
  }
  if (type === "session_log") {
    exactKeys(obj, ["provider", "session_id", "start", "end"], ["revision"], label);
    const start = readInt(obj.start, `${label}.start`, 0, SAFE_INT);
    const end = readInt(obj.end, `${label}.end`, start, SAFE_INT);
    return {
      type,
      provider: assertProvider(obj.provider, `${label}.provider`),
      session_id: readToken(obj.session_id, `${label}.session_id`, TOKEN),
      start,
      end,
      revision: obj.revision === undefined || obj.revision === null ? null : assertSha(obj.revision, `${label}.revision`),
    };
  }
  fail(`${label} type is not a closed source variant`);
}

function readWork(value: unknown): WorkRef {
  const obj = readObject(value, ["repository", "candidate_revision", "url", "workflow", "huddles_task", "huddles_run"], "work_ref");
  let workflow: WorkRef["workflow"] = null;
  if (obj.workflow !== undefined && obj.workflow !== null) {
    const flow = readObject(obj.workflow, ["owner", "ref"], "work_ref.workflow");
    workflow = { owner: readToken(flow.owner, "work_ref.workflow.owner", TOKEN), ref: readToken(flow.ref, "work_ref.workflow.ref", TOKEN) };
  }
  return {
    repository: assertHttps(obj.repository, "work_ref.repository"),
    candidate_revision: assertSha(obj.candidate_revision, "work_ref.candidate_revision"),
    url: obj.url === undefined || obj.url === null ? null : assertHttps(obj.url, "work_ref.url"),
    workflow,
    huddles_task: nullableToken(obj.huddles_task, "work_ref.huddles_task", TOKEN),
    huddles_run: nullableToken(obj.huddles_run, "work_ref.huddles_run", TOKEN),
  };
}

function readDerived(value: unknown): DerivedRef[] {
  if (!Array.isArray(value)) fail("derived_from must be an array");
  if (value.length > COOPERATION_LIMITS.lineageRefsMax) fail("too many lineage references");
  const items = value.map((item, index) => {
    const obj = readObject(item, ["namespace", "record_id"], `derived_from[${index}]`);
    return { namespace: readToken(obj.namespace, `derived_from[${index}].namespace`, TOKEN), record_id: readToken(obj.record_id, `derived_from[${index}].record_id`, TOKEN) };
  });
  items.sort((a, b) => (canonicalJson(a) < canonicalJson(b) ? -1 : canonicalJson(a) > canonicalJson(b) ? 1 : 0));
  for (let i = 1; i < items.length; i += 1) {
    if (canonicalJson(items[i - 1]) === canonicalJson(items[i])) fail("duplicate derived_from");
  }
  return items;
}

function readProvenance(value: unknown): Provenance {
  const obj = readObject(value, ["producer_namespace", "original_record_id", "original_run_id", "derived_from"], "provenance");
  return {
    producer_namespace: readToken(obj.producer_namespace, "provenance.producer_namespace", TOKEN),
    original_record_id: readToken(obj.original_record_id, "provenance.original_record_id", TOKEN),
    original_run_id: nullableToken(obj.original_run_id, "provenance.original_run_id", TOKEN),
    derived_from: obj.derived_from === undefined ? [] : readDerived(obj.derived_from),
  };
}

function readHandoff(value: unknown): HandoffPayload {
  const obj = readObject(value, ["summary", "attempted", "uncertainty", "suggested_next_step", "source_refs", "evidence_ids"], "payload");
  if (!Array.isArray(obj.source_refs) || obj.source_refs.length > COOPERATION_LIMITS.sourceRefsMax) fail("too many source references");
  if (!Array.isArray(obj.evidence_ids) || obj.evidence_ids.length > COOPERATION_LIMITS.lineageRefsMax) fail("too many evidence ids");
  const evidence_ids = obj.evidence_ids.map((item, index) => assertRecordId(item, `payload.evidence_ids[${index}]`));
  if (new Set(evidence_ids).size !== evidence_ids.length) fail("duplicate evidence id");
  return {
    summary: readText(obj.summary, "payload.summary", COOPERATION_LIMITS.stringMaxBytes, true, true),
    attempted: readText(obj.attempted, "payload.attempted", COOPERATION_LIMITS.stringMaxBytes, true, true),
    uncertainty: readText(obj.uncertainty, "payload.uncertainty", COOPERATION_LIMITS.stringMaxBytes, true, true),
    suggested_next_step: readText(obj.suggested_next_step, "payload.suggested_next_step", COOPERATION_LIMITS.stringMaxBytes, true, true),
    source_refs: obj.source_refs.map((item, index) => readSource(item, `payload.source_refs[${index}]`, false)),
    evidence_ids,
  };
}

function readOutcome(value: unknown): Outcome {
  const obj = readObject(value, ["class", "value", "unit", "label", "reason", "statement"], "payload.outcome");
  const present = (key: string) => obj[key] !== undefined;
  if (obj.class === "valid_result") {
    if (present("reason") || present("statement")) fail("valid_result cannot carry a failure reason");
    let scalar: number | string | null = null;
    if (present("value") && obj.value !== null) {
      if (typeof obj.value === "string") scalar = readText(obj.value, "payload.outcome.value", COOPERATION_LIMITS.stringMaxBytes, true, true);
      else scalar = readInt(obj.value, "payload.outcome.value", -SAFE_INT, SAFE_INT);
    }
    return {
      class: "valid_result",
      value: scalar,
      unit: present("unit") && obj.unit !== null ? readText(obj.unit, "payload.outcome.unit", COOPERATION_LIMITS.stringMaxBytes, true, true) : null,
      label: present("label") && obj.label !== null ? readText(obj.label, "payload.outcome.label", COOPERATION_LIMITS.stringMaxBytes, true, true) : null,
    };
  }
  if (present("value") || present("unit") || present("label")) fail("only valid_result carries a scalar result");
  if (obj.class === "invalid_candidate") {
    if (present("statement")) fail("unknown field statement");
    return { class: "invalid_candidate", reason: readText(obj.reason, "payload.outcome.reason", COOPERATION_LIMITS.stringMaxBytes, true, false) };
  }
  if (obj.class === "inconclusive_execution") {
    if (present("statement")) fail("unknown field statement");
    const reason = readText(obj.reason, "payload.outcome.reason", COOPERATION_LIMITS.stringMaxBytes, false, false);
    if (!(INCONCLUSIVE_REASONS as readonly string[]).includes(reason)) fail("inconclusive reason is not a closed value");
    return { class: "inconclusive_execution", reason: reason as InconclusiveReason };
  }
  if (obj.class === "assertion") {
    if (present("reason")) fail("unknown field reason");
    return { class: "assertion", statement: readText(obj.statement, "payload.outcome.statement", COOPERATION_LIMITS.stringMaxBytes, true, false) };
  }
  fail("outcome class is not one of the four evidence results");
}

function readEvidence(value: unknown): EvidencePayload {
  const obj = readObject(value, ["requirement_ref", "spec_ref", "candidate_revision", "evaluator_ref", "run_id", "source_ref", "outcome"], "payload");
  const requirement = readObject(obj.requirement_ref, ["property"], "payload.requirement_ref");
  const outcome = readOutcome(obj.outcome);
  const evaluated = outcome.class !== "assertion";
  let evaluator_ref: EvidencePayload["evaluator_ref"] = null;
  if (obj.evaluator_ref !== undefined && obj.evaluator_ref !== null) {
    const evaluator = readObject(obj.evaluator_ref, ["identity", "revision"], "payload.evaluator_ref");
    evaluator_ref = { identity: readToken(evaluator.identity, "payload.evaluator_ref.identity", TOKEN), revision: readToken(evaluator.revision, "payload.evaluator_ref.revision", TOKEN) };
  } else if (evaluated) fail("evaluated evidence requires evaluator_ref");
  const run_id = nullableToken(obj.run_id, "payload.run_id", TOKEN);
  if (evaluated && run_id === null) fail("evaluated evidence requires run_id");
  const spec = readText(obj.spec_ref, "payload.spec_ref", COOPERATION_LIMITS.locatorMaxBytes, false, false);
  const spec_ref = spec.startsWith("doc://") ? assertBrief(spec, "payload.spec_ref") : assertHttps(spec, "payload.spec_ref");
  const source = readSource(obj.source_ref, "payload.source_ref", true);
  if (source.type !== "artifact" && source.type !== "session_log") fail("evidence source must be an artifact or a positioned log");
  return {
    requirement_ref: { property: readText(requirement.property, "payload.requirement_ref.property", COOPERATION_LIMITS.stringMaxBytes, true, false) },
    spec_ref,
    candidate_revision: assertSha(obj.candidate_revision, "payload.candidate_revision"),
    evaluator_ref,
    run_id,
    source_ref: source,
    outcome,
  };
}

function lineageCount(replyTo: string | null, supersedes: string | null, evidenceIds: readonly string[], derived: readonly DerivedRef[]): number {
  return (replyTo ? 1 : 0) + (supersedes ? 1 : 0) + evidenceIds.length + derived.length;
}

export function validateAppendContext(input: unknown): ValidatedAppend {
  const obj = readObject(input, ["schema_version", "kind", "project", "idempotency_key", "ttl_seconds", "work_ref", "provenance", "reply_to", "supersedes", "payload"], "append_context");
  if (obj.schema_version !== 1) fail("schema_version must be 1");
  if (obj.kind !== "handoff" && obj.kind !== "evidence_reference") fail("kind must be handoff or evidence_reference");
  const project = readToken(obj.project, "project", PROJECT);
  const idempotency_key = readText(obj.idempotency_key, "idempotency_key", COOPERATION_LIMITS.idempotencyKeyMaxBytes, false, false);
  if (!TOKEN.test(idempotency_key)) fail("idempotency_key has an invalid shape");
  const ttl_seconds = optionalInt(obj.ttl_seconds, "ttl_seconds", COOPERATION_LIMITS.ttlMinSeconds, COOPERATION_LIMITS.ttlMaxSeconds, COOPERATION_LIMITS.ttlDefaultSeconds);
  const work_ref = readWork(obj.work_ref);
  const provenance = readProvenance(obj.provenance);
  const reply_to = obj.reply_to === undefined || obj.reply_to === null ? null : assertRecordId(obj.reply_to, "reply_to");
  const supersedes = obj.supersedes === undefined || obj.supersedes === null ? null : assertRecordId(obj.supersedes, "supersedes");
  if (reply_to && supersedes && reply_to === supersedes) fail("reply_to and supersedes must be distinct links");
  const payload = obj.kind === "handoff" ? readHandoff(obj.payload) : readEvidence(obj.payload);
  const evidenceIds = payload && "evidence_ids" in payload ? payload.evidence_ids : [];
  if (lineageCount(reply_to, supersedes, evidenceIds, provenance.derived_from) > COOPERATION_LIMITS.lineageRefsMax) fail("too many lineage references");
  const validated: ValidatedAppend = { schema_version: 1, kind: obj.kind, project, idempotency_key, ttl_seconds, work_ref, provenance, reply_to, supersedes, payload };
  if (utf8Bytes(canonicalJson(validated)) > COOPERATION_LIMITS.recordMaxBytes) fail("record exceeds 8 KiB");
  return validated;
}

export function validateQueryContext(input: unknown): ValidatedQuery {
  const obj = readObject(input, ["schema_version", "project", "query", "work_ref", "kinds", "limit", "max_bytes", "cutoff", "ids", "include_expired"], "query_context");
  if (obj.schema_version !== 1) fail("schema_version must be 1");
  const project = readToken(obj.project, "project", PROJECT);
  const max_bytes = optionalInt(obj.max_bytes, "max_bytes", COOPERATION_LIMITS.responseMinBytes, COOPERATION_LIMITS.responseMaxBytes, COOPERATION_LIMITS.responseDefaultBytes);
  const historical = obj.ids !== undefined || obj.include_expired !== undefined;
  const searchFields = ["query", "work_ref", "kinds", "limit", "cutoff"].filter((key) => obj[key] !== undefined);
  if (historical && searchFields.length) fail("historical and search modes cannot be combined");
  if (historical) {
    if (!Array.isArray(obj.ids) || obj.ids.length < 1 || obj.ids.length > COOPERATION_LIMITS.exactIdsMax) fail("ids must contain 1 to 30 full ids");
    const ids = obj.ids.map((item, index) => assertRecordId(item, `ids[${index}]`));
    if (new Set(ids).size !== ids.length) fail("duplicate id");
    if (obj.include_expired !== true) fail("historical reads set include_expired true");
    return { mode: "historical", schema_version: 1, project, ids, include_expired: true, max_bytes };
  }
  let work_ref: { repository: string | null; candidate_revision: string | null } | null = null;
  if (obj.work_ref !== undefined) {
    const work = readObject(obj.work_ref, ["repository", "candidate_revision"], "work_ref");
    work_ref = {
      repository: work.repository === undefined ? null : assertHttps(work.repository, "work_ref.repository"),
      candidate_revision: work.candidate_revision === undefined ? null : assertSha(work.candidate_revision, "work_ref.candidate_revision"),
    };
    if (work_ref.repository === null && work_ref.candidate_revision === null) fail("work_ref needs a repository or a revision");
  }
  let kinds: CooperationKind[] | null = null;
  if (obj.kinds !== undefined) {
    if (!Array.isArray(obj.kinds) || obj.kinds.length < 1 || obj.kinds.length > 2) fail("kinds must list one or both record kinds");
    if (!obj.kinds.every((item) => item === "handoff" || item === "evidence_reference")) fail("kinds contains an unknown value");
    if (new Set(obj.kinds).size !== obj.kinds.length) fail("duplicate kind");
    kinds = obj.kinds as CooperationKind[];
  }
  return {
    mode: "search",
    schema_version: 1,
    project,
    query: obj.query === undefined ? "" : readText(obj.query, "query", COOPERATION_LIMITS.queryTextMaxBytes, true, true),
    work_ref,
    kinds,
    limit: optionalInt(obj.limit, "limit", COOPERATION_LIMITS.resultMin, COOPERATION_LIMITS.resultMax, COOPERATION_LIMITS.resultDefault),
    max_bytes,
    cutoff: obj.cutoff === undefined ? null : assertTimestamp(obj.cutoff, "cutoff"),
  };
}

export function causalKey(provenance: Provenance): { producer_namespace: string; original_record_id: string; original_run_id: string | null } {
  return {
    producer_namespace: provenance.producer_namespace,
    original_record_id: provenance.original_record_id,
    original_run_id: provenance.original_run_id,
  };
}

function fingerprintMaterial(payload: EvidencePayload): Record<string, unknown> {
  const outcome = payload.outcome;
  return {
    candidate_revision: payload.candidate_revision,
    evaluator_identity: payload.evaluator_ref?.identity ?? null,
    evaluator_revision: payload.evaluator_ref?.revision ?? null,
    outcome_class: outcome.class,
    outcome_reason: outcome.class === "assertion" ? outcome.statement : outcome.class === "valid_result" ? null : outcome.reason,
    requirement_property: payload.requirement_ref.property,
    result_label: outcome.class === "valid_result" ? outcome.label : null,
    result_unit: outcome.class === "valid_result" ? outcome.unit : null,
    result_value: outcome.class === "valid_result" ? outcome.value : null,
    run_id: payload.run_id,
    source_ref: payload.source_ref,
    spec_ref: payload.spec_ref,
  };
}

export function evidenceFingerprint(payload: EvidencePayload): string {
  return sha256Hex(canonicalJson(fingerprintMaterial(payload)));
}

export function clientPayloadDigest(input: ValidatedAppend): string {
  return sha256Hex(canonicalJson(input));
}

export function lineageRelation(left: EvidencePayload, leftProvenance: Provenance, right: EvidencePayload, rightProvenance: Provenance): "duplicate" | "conflict" | "distinct" {
  if (canonicalJson(causalKey(leftProvenance)) !== canonicalJson(causalKey(rightProvenance))) return "distinct";
  return evidenceFingerprint(left) === evidenceFingerprint(right) ? "duplicate" : "conflict";
}

function readLabel(value: unknown, label: string): string | null {
  if (value === undefined || value === null) return null;
  return readText(value, label, 256, false, false);
}

export interface StampInput {
  id: string;
  principal: string;
  origin: string;
  now: string;
  client_label: string | null;
  session_id: string | null;
  input: ValidatedAppend;
}

export function stampRecord(stamp: StampInput): CooperationRecord {
  if (stamp.principal === null || stamp.principal === undefined || stamp.principal === "") {
    throw new CooperationError("UNAUTHENTICATED", "authenticated principal is required");
  }
  const author_principal = readText(stamp.principal, "principal", 256, false, false);
  const origin = readToken(stamp.origin, "origin", PROJECT);
  if (origin !== stamp.input.project) fail("origin does not match the validated project");
  const now = assertTimestamp(stamp.now, "now");
  const record: CooperationRecord = {
    id: assertRecordId(stamp.id, "id"),
    schema_version: 1,
    kind: stamp.input.kind,
    author_principal,
    client_label: readLabel(stamp.client_label, "client_label"),
    session_id: stamp.session_id === null ? null : readToken(stamp.session_id, "session_id", TOKEN),
    origin,
    created_at: now,
    expires_at: addSeconds(now, stamp.input.ttl_seconds),
    audience: { kind: "principal", principal: author_principal },
    idempotency_key: stamp.input.idempotency_key,
    work_ref: stamp.input.work_ref,
    reply_to: stamp.input.reply_to,
    supersedes: stamp.input.supersedes,
    provenance: { ...stamp.input.provenance, reported: true },
    payload: stamp.input.payload,
  };
  if (utf8Bytes(canonicalJson(record)) > COOPERATION_LIMITS.recordMaxBytes) fail("stamped record exceeds 8 KiB");
  return record;
}

export function recordRevision(record: CooperationRecord): string {
  return sha256Hex(canonicalJson(record));
}

export function visibleTo(record: CooperationRecord, principal: string, origin: string): boolean {
  return record.audience.kind === "principal"
    && record.audience.principal === principal
    && record.author_principal === principal
    && record.origin === origin;
}

export function hiddenPredecessors(records: readonly { id: string; supersedes: string | null; created_at: string }[], cutoff: string): string[] {
  const limit = assertTimestamp(cutoff, "cutoff");
  const hidden = new Set<string>();
  for (const record of records) {
    if (record.supersedes && assertTimestamp(record.created_at, "created_at") <= limit) hidden.add(record.supersedes);
  }
  return [...hidden].sort();
}

function searchableText(record: CooperationRecord): string {
  if (record.kind === "handoff") {
    const payload = record.payload as HandoffPayload;
    return [payload.summary, payload.attempted, payload.uncertainty, payload.suggested_next_step].join("\n");
  }
  const payload = record.payload as EvidencePayload;
  const outcome = payload.outcome;
  const tail = outcome.class === "assertion" ? outcome.statement : outcome.class === "valid_result" ? `${outcome.label ?? ""}\n${outcome.unit ?? ""}\n${outcome.value ?? ""}` : outcome.reason;
  return [payload.requirement_ref.property, payload.spec_ref, outcome.class, tail].join("\n");
}

export interface SelectionCriteria {
  query: string;
  repository: string | null;
  candidate_revision: string | null;
}

export function compareSelection(left: CooperationRecord, right: CooperationRecord, criteria: SelectionCriteria): number {
  const rank = (record: CooperationRecord) => {
    const exact = criteria.repository === null && criteria.candidate_revision === null
      ? 0
      : (criteria.repository === null || record.work_ref.repository === criteria.repository)
        && (criteria.candidate_revision === null || record.work_ref.candidate_revision === criteria.candidate_revision)
        ? 0
        : 1;
    const lexical = criteria.query === "" || searchableText(record).includes(criteria.query) ? 0 : 1;
    return [exact, lexical, record.created_at, record.id] as const;
  };
  const a = rank(left);
  const b = rank(right);
  if (a[0] !== b[0]) return a[0] - b[0];
  if (a[1] !== b[1]) return a[1] - b[1];
  if (a[2] !== b[2]) return a[2] < b[2] ? 1 : -1;
  if (a[3] !== b[3]) return a[3] < b[3] ? -1 : 1;
  return 0;
}

export function sealContribution(draft: Omit<ContextContribution, "budget_bytes">): ContextContribution {
  let budget_bytes = 0;
  let current = { ...draft, budget_bytes } as ContextContribution;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const bytes = utf8Bytes(canonicalJson(current));
    if (bytes === budget_bytes) return current;
    budget_bytes = bytes;
    current = { ...draft, budget_bytes };
  }
  fail("contribution budget did not stabilize");
}

function assertStampedRecord(value: unknown): CooperationRecord {
  const obj = readObject(value, ["id", "schema_version", "kind", "author_principal", "client_label", "session_id", "origin", "created_at", "expires_at", "audience", "idempotency_key", "work_ref", "reply_to", "supersedes", "provenance", "payload"], "record");
  if (obj.schema_version !== 1) fail("schema_version must be 1");
  const audience = readObject(obj.audience, ["kind", "principal"], "audience");
  if (audience.kind !== "principal") fail("audience.kind must be principal");
  const provenance = readObject(obj.provenance, ["producer_namespace", "original_record_id", "original_run_id", "derived_from", "reported"], "provenance");
  if (provenance.reported !== true) fail("provenance.reported must be true");
  const created_at = assertTimestamp(String(obj.created_at), "created_at");
  const expires_at = assertTimestamp(String(obj.expires_at), "expires_at");
  const span = (Date.parse(expires_at) - Date.parse(created_at)) / 1000;
  if (!Number.isInteger(span) || span < COOPERATION_LIMITS.ttlMinSeconds || span > COOPERATION_LIMITS.ttlMaxSeconds) fail("expiry is outside the TTL bounds");
  const record = obj as unknown as CooperationRecord;
  if (utf8Bytes(canonicalJson(record)) > COOPERATION_LIMITS.recordMaxBytes) fail("record exceeds 8 KiB");
  if (audience.principal !== obj.author_principal) fail("audience principal differs from the author");
  return record;
}

export function validateContribution(input: unknown): ContextContribution {
  const obj = readObject(input, ["schema_version", "as_of", "cutoff", "selector_version", "records", "selected", "max_bytes", "budget_bytes", "omissions", "diagnostics", "window_capped", "candidate_window_exhaustive"], "contribution");
  if (obj.schema_version !== 1) fail("schema_version must be 1");
  if (obj.selector_version !== SELECTOR_VERSION) fail("selector_version is not the v1 selector");
  const as_of = assertTimestamp(String(obj.as_of), "as_of");
  const cutoff = assertTimestamp(String(obj.cutoff), "cutoff");
  if (!Array.isArray(obj.records) || obj.records.length > COOPERATION_LIMITS.resultMax) fail("too many records");
  const records = obj.records.map((item) => assertStampedRecord(item));
  if (!Array.isArray(obj.selected) || obj.selected.length !== records.length) fail("selected manifest does not match records");
  obj.selected.forEach((item, index) => {
    const selected = readObject(item, ["id", "revision", "fresh"], `selected[${index}]`);
    if (selected.id !== records[index].id) fail("selected id is out of order");
    if (selected.revision !== recordRevision(records[index])) fail("selected revision does not match the record");
    if (selected.fresh !== isFresh(records[index].expires_at, as_of)) fail("fresh label does not match expiry");
  });
  const max_bytes = readInt(obj.max_bytes, "max_bytes", COOPERATION_LIMITS.responseMinBytes, COOPERATION_LIMITS.responseMaxBytes);
  const budget_bytes = readInt(obj.budget_bytes, "budget_bytes", 0, COOPERATION_LIMITS.responseMaxBytes);
  if (budget_bytes > max_bytes) fail("contribution exceeds max_bytes");
  if (utf8Bytes(canonicalJson(obj)) !== budget_bytes) fail("budget_bytes is not the canonical contribution size");
  if (typeof obj.window_capped !== "boolean" || typeof obj.candidate_window_exhaustive !== "boolean") fail("window flags must be booleans");
  if (obj.candidate_window_exhaustive !== !obj.window_capped) fail("a capped window is not exhaustive");
  if (!Array.isArray(obj.omissions)) fail("omissions must be an array");
  const seen = new Set<string>();
  for (const omission of obj.omissions) {
    const item = readObject(omission, ["reason", "count"], "omission");
    if (item.reason !== "over_byte_budget" && item.reason !== "over_result_limit") fail("unknown omission reason");
    readInt(item.count, "omission.count", 0, COOPERATION_LIMITS.populationMax);
    if (seen.has(String(item.reason))) fail("duplicate omission reason");
    seen.add(String(item.reason));
  }
  if (!Array.isArray(obj.diagnostics) || obj.diagnostics.length > COOPERATION_LIMITS.diagnosticsMax) fail("too many diagnostics");
  for (const diagnostic of obj.diagnostics) {
    const item = readObject(diagnostic, ["code", "causal_key", "record_ids", "fingerprints"], "diagnostic");
    if (item.code !== "conflicting_reported_evidence") fail("unknown diagnostic");
    readObject(item.causal_key, ["producer_namespace", "original_record_id", "original_run_id"], "causal_key");
    if (!Array.isArray(item.record_ids) || item.record_ids.length > COOPERATION_LIMITS.diagnosticsMax) fail("diagnostic record ids are unbounded");
    if (!Array.isArray(item.fingerprints) || item.fingerprints.length > COOPERATION_LIMITS.diagnosticsMax) fail("diagnostic fingerprints are unbounded");
  }
  return obj as unknown as ContextContribution;
}

const closedObject = { type: "object", additionalProperties: false };

export const APPEND_CONTEXT_INPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  description: "Append one client-authored handoff or evidence reference. The server stamps the principal, origin, clock, and expiry. This is not a claim and it takes no authority parameter. Reported results stay reported.",
  required: ["schema_version", "kind", "project", "idempotency_key", "work_ref", "provenance", "payload"],
  properties: {
    schema_version: { type: "integer", enum: [1] },
    kind: { type: "string", enum: ["handoff", "evidence_reference"] },
    project: { type: "string" },
    idempotency_key: { type: "string" },
    ttl_seconds: { type: "integer", minimum: 60, maximum: 604800 },
    work_ref: closedObject,
    provenance: closedObject,
    reply_to: { type: "string" },
    supersedes: { type: "string" },
    payload: {
      ...closedObject,
      description: "Handoff text and source refs, or one evidence reference with exactly one of valid_result, invalid_candidate, inconclusive_execution, or assertion.",
    },
  },
} as const;

export const QUERY_CONTEXT_INPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  description: "Return a bounded memory contribution for the authenticated principal and exact origin. Search mode and historical exact-id mode are mutually exclusive. Historical mode sets include_expired true and does not take a query. This takes no authority parameter and does not grant task authority.",
  required: ["schema_version", "project"],
  properties: {
    schema_version: { type: "integer", enum: [1] },
    project: { type: "string" },
    query: { type: "string" },
    work_ref: closedObject,
    kinds: { type: "array", items: { type: "string", enum: ["handoff", "evidence_reference"] } },
    limit: { type: "integer", minimum: 1, maximum: 30 },
    max_bytes: { type: "integer", minimum: 2048, maximum: 32768 },
    cutoff: { type: "string" },
    ids: { type: "array", items: { type: "string" }, maxItems: 30 },
    include_expired: { type: "boolean" },
  },
} as const;
