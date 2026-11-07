// store.ts — the D1 side of kypp/store.py: claim, recall, get, usage, live claims, status writes.
import { type Claim, type ClaimType, type Confirmation, type GroupPlan, SCOPES, type Scope, TYPES, ownedScope, ftsQuery, resolveOrigin } from "./memory.ts";
import { normalizedContent } from "./claim-machine.ts";
import type { ProjectInfo } from "./s1.ts";

const MAX_CONTENT = 4000;
const MAX_REFS = 10;
const REF_KEYS = ["symbol", "path", "query", "repo", "commit"];

// code_refs arrive as untyped tool arguments; store only plain {symbol,path,query,repo,commit} strings
// so a malformed entry can't break rendering later.
export function cleanCodeRefs(refs: unknown): Record<string, string>[] {
  if (refs === undefined || refs === null) return [];
  if (!Array.isArray(refs) || refs.length > MAX_REFS) throw new Error(`code_refs must be a list of at most ${MAX_REFS} objects`);
  return refs.map((r) => {
    if (!r || typeof r !== "object" || Array.isArray(r)) throw new Error("each code_ref must be an object like {symbol, path, query}");
    const out: Record<string, string> = {};
    for (const k of REF_KEYS) {
      const v = (r as Record<string, unknown>)[k];
      if (v === undefined || v === null) continue;
      if (typeof v !== "string" || v.length > 300) throw new Error(`code_ref.${k} must be a string of at most 300 chars`);
      out[k] = v;
    }
    if (!Object.keys(out).length) throw new Error("a code_ref needs at least one of symbol, path or query");
    return out;
  });
}
const now = () => new Date().toISOString();
const uid = () => crypto.randomUUID().replaceAll("-", "");
const POINTER_SQL = "INSERT OR IGNORE INTO code_pointers(id,claim_id,entry_ref,project,doc_id,anchor,revision,path,symbol,kind,created_at)";
const DOC_REF = /doc:\/\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)@(latest|current|\d{1,4})#([A-Za-z0-9_-]+)/g;
const DOC_REF_ONE = new RegExp(DOC_REF.source);
const DOC_REF_WHOLE = new RegExp(`^${DOC_REF.source}$`);
const LEADING_DOT = /^\.\//;
const WORD_RUN = /[^\s]+/g;
const CODE_FILE = /\.(py|ts|md|rs|go|sql)$/;
const KEBAB = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;
const HANDLE = /^[0-9a-f]{8,32}$/;
// The columns a Claim carries; metadata and file_tried_at stay in the table for the passes that need them.
const CLAIM_COLUMNS = ["id", "type", "subject", "content", "scope", "project", "origin", "session", "agent", "user",
  "status", "authority", "confidence", "source_ids", "code_refs", "verify", "created_at", "updated_at"];
const claimColumns = (t?: string) => CLAIM_COLUMNS.map((c) => (t ? `${t}.${c}` : c)).join(", ");

// A claim's key is a feature ref; a file path is a hint, and line numbers are not stored.
function pointerRow(claimId: string, entryRef: string, path: string, symbol: string, kind: string,
  project = "", docId = "", revision = "", anchor = ""): unknown[] {
  if (!project && entryRef.startsWith("doc://")) {
    const m = DOC_REF_ONE.exec(entryRef);
    if (m) { project = m[1]; docId = m[2]; revision = m[3]; anchor = m[4]; }
  } else if (!anchor && (entryRef.startsWith("symbol:") || entryRef.startsWith("path:"))) {
    docId = "code";
    anchor = entryRef.slice(entryRef.indexOf(":") + 1);
  }
  return [uid(), claimId, entryRef, project, docId, anchor, revision, path.replace(LEADING_DOT, ""), symbol, kind, now()];
}

function pointerRows(claimId: string, subject: string, content: string, refs: Record<string, string>[]): unknown[][] {
  const rows: unknown[][] = [];
  const paths = refs.filter((r) => r.path).map((r) => [r.path, r.symbol ?? ""] as const);
  for (const ref of refs) {
    const path = ref.path ?? "";
    const symbol = ref.symbol ?? "";
    if (!path && !symbol) continue;
    const entry = symbol ? `symbol:${symbol}` : `path:${path}`;
    rows.push(pointerRow(claimId, entry, path, symbol, "code_ref"));
  }
  for (const match of `${subject}\n${content}`.matchAll(DOC_REF)) {
    const targets = paths.length ? paths : [["", ""] as const];
    for (const [path, symbol] of targets) {
      rows.push(pointerRow(claimId, match[0], path, symbol, "context", match[1], match[2], match[3], match[4]));
    }
  }
  return rows;
}

// A path, a kebab anchor, or a doc:// ref is an identity, not an FTS token. The clauses match
// kypp/store.py `_pointer_claim_ids`: exact path, anchor, or any revision of the same anchor.
function pointerMatch(query: string): [string, unknown[]] {
  const clauses: string[] = [];
  const binds: unknown[] = [];
  for (const m of query.matchAll(DOC_REF)) {
    clauses.push("(entry_ref = ? OR (project = ? AND doc_id = ? AND anchor = ?))");
    binds.push(m[0], m[1], m[2], m[4]);
  }
  for (const tok of query.match(WORD_RUN) ?? []) {
    const bare = tok.replace(LEADING_DOT, "");
    if (bare.includes("/") || CODE_FILE.test(bare)) {
      clauses.push("path = ?");
      binds.push(bare);
    } else if (KEBAB.test(bare)) {
      clauses.push("anchor = ?");
      binds.push(bare);
    }
  }
  if (!clauses.length) {
    const text = query.trim();
    clauses.push("(path = ? OR anchor = ? OR entry_ref = ? OR claim_id = ?)");
    binds.push(text, text, text, text);
  }
  return [clauses.join(" OR "), binds];
}

function hydrate(r: Record<string, unknown>): Claim {
  return {
    ...(r as unknown as Claim),
    source_ids: JSON.parse((r.source_ids as string) || "[]"),
    code_refs: JSON.parse((r.code_refs as string) || "[]"),
  };
}

const UNSORTED = "scope = 'project' AND project IS NULL";
// A session's verdict is its newest rating of the claim; it confirms only if that rating is `used` and marked writable.
const CONFIRMING = "CASE WHEN json_extract(u.metadata, '$.writable') = 1 THEN u.score END";

/** A claim's level, keyed the way its group is: a project, a person's own scope, or a room's lane. */
export type Lane = { project: string } | { user: string } | { room: string };
/** An earlier claim of this session's with the same content under another subject, and its provenance. */
/** A standing `wrong` rating on a claim: the rater's note and when it was made. */
export interface Dispute { note: string | null; at: string }
export interface Renamed { id: string; subject: string; stamped: boolean; echoOf: string[] }
const laneSql = (at: Lane): [string, string] => "project" in at ? ["c.scope = 'project' AND c.project = ?", at.project]
  : "room" in at ? ["c.scope = 'room' AND c.user = ?", at.room] : ["c.scope = 'user' AND c.user = ?", at.user];

// Which unconfirmed (candidate) claims a read returns besides accepted memory: none (briefing),
// other agents' claims in the caller's own project (the default), or candidates at every level.
export type Candidates = "none" | "origin" | "all";
// A read's reply as shown: [section label, its claims in order], in the order the reply renders them.
export type Shown = [section: string, claims: Claim[]][];

// What a caller sees: this project, the groups it rolls up to, their own user scope, the global
// scope, and the unsorted claims their own session wrote; never superseded/rejected history.
// `t` is the table alias.
// Reads cover every level the caller rolls up to; other sessions' candidates show only in the caller's own project.
// An unsorted claim is visible only to the session (and user) that wrote it until it is filed.
function visible(t: string, project: string | null, groups: string[], user: string | null, candidates: Candidates,
  session: string | null): [string, unknown[]] {
  const levels = [`${t}.scope = 'global'`];
  const params: unknown[] = [];
  if (project) { levels.push(`(${t}.scope = 'project' AND ${t}.project = ?)`); params.push(project); }
  if (groups.length) { levels.push(`(${t}.scope = 'group' AND ${t}.project IN (${groups.map(() => "?").join(",")}))`); params.push(...groups); }
  if (user) { levels.push(`(${t}.scope = 'user' AND ${t}.user = ?)`); params.push(user); }
  const status = candidates === "all" ? `${t}.status IN ('candidate','accepted')` : `${t}.status = 'accepted'`;
  const parts = [`(${status} AND (${levels.join(" OR ")}))`];
  if (candidates === "origin" && project) {
    parts.push(`(${t}.status = 'candidate' AND ${t}.scope = 'project' AND ${t}.project = ?${session ? ` AND ${t}.session IS NOT ?` : ""})`);
    params.push(project, ...(session ? [session] : []));
  }
  // Session ids aren't bound to a caller, so the row's user must match too: a replayed session
  // header can't read someone else's unsorted claims.
  if (session) {
    parts.push(`(${t}.status = 'candidate' AND ${t}.scope = 'project' AND ${t}.project IS NULL AND ${t}.session = ? AND ${t}.user IS ?)`);
    params.push(session, user);
  }
  return [`(${parts.join(" OR ")})`, params];
}

// Another session's candidate shows only once the S1 gate has judged it: the gate fails open, and
// until defrag's judgeLate runs, an unjudged claim could be control text aimed at other agents.
function judgedOnly(t: string, session: string | null): [string, unknown[]] {
  // Sessionless callers share no session: NULL = NULL would let every one of them read the others' claims.
  return [` AND (${t}.status != 'candidate' OR (${t}.session IS NOT NULL AND ${t}.session = ?) OR json_extract(${t}.metadata, '$.s1') IS NOT NULL)`, [session]];
}

/** A room bot's reach: its own lane, plus the projects its room is linked to and their groups. */
export interface RoomScope { identity: string; links: string[]; groups: string[] }

// A room reads its own lane (its unconfirmed lessons too: the room's bots are the whole audience) and its linked origins' accepted memory; never user scope or global.
function roomVisible(t: string, room: RoomScope, candidates: Candidates): [string, unknown[]] {
  const status = candidates === "none" ? `${t}.status = 'accepted'` : `${t}.status IN ('candidate','accepted')`;
  const parts = [`(${status} AND ${t}.scope = 'room' AND ${t}.user = ?)`];
  const params: unknown[] = [room.identity];
  const at = candidates === "all" ? `${t}.status IN ('candidate','accepted')` : `${t}.status = 'accepted'`;
  if (room.links.length) { parts.push(`(${at} AND ${t}.scope = 'project' AND ${t}.project IN (${room.links.map(() => "?").join(",")}))`); params.push(...room.links); }
  if (room.groups.length) { parts.push(`(${at} AND ${t}.scope = 'group' AND ${t}.project IN (${room.groups.map(() => "?").join(",")}))`); params.push(...room.groups); }
  return [`(${parts.join(" OR ")})`, params];
}

export interface ClaimInput {
  type: ClaimType;
  subject: string;
  content: string;
  scope: Scope;
  project: string | null; // a registered project, or null to leave a project-scope claim unsorted
  origin?: string | null;
  session?: string | null;
  confidence: number;
  sourceIds: string[];
  codeRefs: unknown;
  accept: boolean;
  agent: string | null;
  user: string | null;
  metadata?: Record<string, unknown>;
  /** A room sharing into `project`: the write lands only while that room is still linked to it. */
  sharedFrom?: string;
}

export const PURGED = "[purged]";

export class D1Store {
  private db: D1Database;
  private screened: boolean;
  // `screened` (an S1 model is wired) hides other sessions' candidates the gate has not judged yet.
  constructor(db: D1Database, opts: { screened?: boolean } = {}) { this.db = db; this.screened = opts.screened ?? false; }

  private screen(t: string, session: string | null): [string, unknown[]] {
    return this.screened ? judgedOnly(t, session) : ["", []];
  }

  // Claims written here are agent authority; human and verified come only from operatorClaim/markVerified (POST /v1/operator) and authenticated POST /sync imports, once built.
  async claim(c: ClaimInput): Promise<string> {
    if (!TYPES.includes(c.type)) throw new Error(`bad type ${c.type}`);
    if (!SCOPES.includes(c.scope)) throw new Error(`bad scope ${c.scope}`);
    if (ownedScope(c.scope) && !c.user) throw new Error(`a ${c.scope}-scoped claim needs a signed-in ${c.scope}`);
    if (!c.subject.trim() || !c.content.trim()) throw new Error("subject and content are required");
    if (c.content.length > MAX_CONTENT) throw new Error(`content over ${MAX_CONTENT} chars; distill it`);
    const id = uid(), ts = now();
    const confidence = Math.min(1, Math.max(0, Number(c.confidence) || 0));
    const codeRefs = cleanCodeRefs(c.codeRefs);
    // Why: a room's share is checked against its links before the System One judge runs; the links
    // can change meanwhile, so the write itself re-checks the link it shares through.
    // A lesson leaves the room only into a currently linked origin.
    const linked = c.sharedFrom === undefined ? { sql: "", args: [] as unknown[] }
      : { sql: " WHERE EXISTS (SELECT 1 FROM room_links WHERE room = ? AND project = ?)", args: [c.sharedFrom, c.project] };
    const [inserted] = await this.db.batch([
      this.db.prepare(
        "INSERT INTO memory_claims(id,type,subject,content,scope,project,origin,session,agent,user,status,authority,confidence,source_ids,code_refs,metadata,created_at,updated_at)"
        + ` SELECT ?,?,?,?,?,?,?,?,?,?,?,'agent',?,?,?,?,?,?${linked.sql}`,
      ).bind(id, c.type, c.subject, c.content, c.scope, c.scope === "project" ? c.project : null, c.origin ?? c.project, c.session ?? null, c.agent, c.user,
        c.accept ? "accepted" : "candidate", confidence, JSON.stringify(c.sourceIds), JSON.stringify(codeRefs),
        JSON.stringify(c.metadata ?? {}), ts, ts, ...linked.args),
      this.db.prepare("INSERT INTO claims_fts(claim_id, subject, content) SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM memory_claims WHERE id = ?)")
        .bind(id, c.subject, c.content, id),
      ...pointerRows(id, c.subject, c.content, codeRefs).map((row) => this.db.prepare(
        POINTER_SQL + " SELECT ?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM memory_claims WHERE id = ?)",
      ).bind(...row, id)),
    ]);
    if (!inserted.meta.changes) throw new Error(`this room is no longer linked to ${c.project}, so the lesson stays out of it`);
    return id;
  }

  // Only POST /v1/operator calls this; it is the hosted writer of human authority and of verify commands.
  // Correction explicitly replaces this group's live rows, including its audit, in the insert batch.
  // It is not a consolidation rank contest: tied clocks or lower confidence cannot discard it.
  // With a key, the write lands at most once: the receipt is inserted in the claim's own batch, so a
  // resend of the key (after a lost answer) returns the first claim instead of inserting another.
  async operatorClaim(c: ClaimInput, op: "remember" | "correct", verify: string | null, key: string | null = null): Promise<{ id: string; superseded: number; replayed: boolean }> {
    if (!TYPES.includes(c.type)) throw new Error(`bad type ${c.type}`);
    if (!SCOPES.includes(c.scope) || c.scope === "group" || c.scope === "room") throw new Error(`bad scope ${c.scope}`);
    if (c.scope === "project" && !c.project) throw new Error("a project-scoped operator claim needs a known project");
    if (!c.subject.trim() || !c.content.trim()) throw new Error("subject and content are required");
    if (c.content.length > MAX_CONTENT) throw new Error(`content over ${MAX_CONTENT} chars; distill it`);
    const id = uid(), ts = now();
    const authority = op === "correct" ? "human" : "agent";
    const project = c.scope === "project" ? c.project : null;
    const confidence = Math.min(1, Math.max(0, Number(c.confidence) || 0));
    const codeRefs = cleanCodeRefs(c.codeRefs);
    const fingerprint = key ? await sha256(JSON.stringify([op, c.type, c.subject, c.content, c.scope, project,
      c.project, confidence, !!c.accept, verify, c.agent, c.user])) : "";
    const replay = async () => {
      const r = await this.db.prepare("SELECT fingerprint, claim_id FROM operator_receipts WHERE key = ?").bind(key).first<{ fingerprint: string; claim_id: string }>();
      if (r && r.fingerprint !== fingerprint) throw new Error(`key ${JSON.stringify(key)} was already used for a different write`);
      return r ? { id: r.claim_id, superseded: 0, replayed: true } : null;
    };
    if (key) {
      const prior = await replay();
      if (prior) return prior;
    }
    const statements = [
      ...(key ? [this.db.prepare("INSERT INTO operator_receipts(key, fingerprint, claim_id, created_at) VALUES(?,?,?,?)")
        .bind(key, fingerprint, id, ts)] : []),
      this.db.prepare(
        "INSERT INTO memory_claims(id,type,subject,content,scope,project,origin,session,agent,user,status,authority,confidence,source_ids,code_refs,metadata,verify,created_at,updated_at)"
        + " VALUES(?,?,?,?,?,?,?,NULL,?,?,?,?,?,'[]',?,'{}',?,?,?)",
      ).bind(id, c.type, c.subject, c.content, c.scope, project, c.project, c.agent, c.user,
        c.accept || authority === "human" ? "accepted" : "candidate", authority, confidence, JSON.stringify(codeRefs), verify, ts, ts),
      this.db.prepare("INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)").bind(id, c.subject, c.content),
      this.logOperator(id, op, project, `authority=${authority}${verify ? " verify" : ""}`),
      ...pointerRows(id, c.subject, c.content, codeRefs).map((row) => this.db.prepare(
        POINTER_SQL + " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      ).bind(...row)),
    ];
    if (op === "correct") {
      const prior = "status IN ('candidate','accepted') AND subject = ? AND scope = ? AND project IS ?"
        + " AND (scope != 'user' OR user IS ?) AND id != ?";
      const key = [c.subject, c.scope, project, c.user, id];
      statements.push(
        this.db.prepare(
          "INSERT INTO defrag_log(id, claim_id, action, from_project, to_project, score, reason, created_at)"
          + " SELECT lower(hex(randomblob(16))), id, 'operator:supersede', NULL, NULL, NULL, ?, ?"
          + ` FROM memory_claims WHERE ${prior}`,
        ).bind(`by ${id}`, ts, ...key),
        this.db.prepare(`UPDATE memory_claims SET status='superseded', updated_at=? WHERE ${prior}`).bind(ts, ...key),
      );
    }
    let results;
    try {
      results = await this.db.batch(statements);
    } catch (e) {
      // A concurrent send of the same key committed first: the receipt's primary key rolled this batch back.
      const prior = key ? await replay() : null;
      if (prior) return prior;
      throw e;
    }
    return { id, superseded: op === "correct" ? results[results.length - 1].meta.changes : 0, replayed: false };
  }

  // Every claim the operator's `kypp verify` re-checks in this project: any status, so a rejected
  // claim revives when its check passes again, except a superseded human claim (as kypp/store.py).
  async verifiable(project: string, user: string | null, subject?: string): Promise<{ id: string; subject: string; verify: string }[]> {
    let sql = "SELECT id, subject, verify FROM memory_claims WHERE verify IS NOT NULL AND NOT (authority = 'human' AND status = 'superseded')"
      + " AND ((scope = 'project' AND project = ?) OR scope = 'global' OR (scope = 'user' AND user IS ?))";
    const binds: unknown[] = [project, user];
    if (subject) { sql += " AND subject = ?"; binds.push(subject); }
    const { results } = await this.db.prepare(sql).bind(...binds).all<{ id: string; subject: string; verify: string }>();
    return results;
  }

  // Verify raises agent → verified, never lowers human.
  // The hosted kypp/store.py mark_verified, logged: pass → accepted at verified authority (a human claim
  // keeps human and its updated_at); fail → rejected. A superseded human claim stays superseded.
  async markVerified(id: string, passed: boolean): Promise<boolean> {
    const status = `CASE WHEN authority = 'human' AND status = 'superseded' THEN status ELSE '${passed ? "accepted" : "rejected"}' END`;
    const [r] = await this.db.batch([
      this.db.prepare(
        `UPDATE memory_claims SET status = ${status},`
        + (passed ? " authority = CASE WHEN authority = 'human' THEN 'human' ELSE 'verified' END," : "")
        + " updated_at = CASE WHEN authority = 'human' THEN updated_at ELSE ? END WHERE id = ? AND verify IS NOT NULL",
      ).bind(now(), id),
      this.logOperator(id, "verify", null, passed ? "passed" : "failed", "SELECT 1 FROM memory_claims WHERE id = ? AND verify IS NOT NULL"),
    ]);
    return r.meta.changes > 0;
  }

  // Takes a claim out of recall by handle, keeping a superseded claim superseded (as kypp/store.py
  // reject), with the reason in its metadata and a defrag_log row.
  async operatorReject(id: string, reason: string | null): Promise<void> {
    await this.db.batch([
      this.db.prepare(
        "UPDATE memory_claims SET status = CASE WHEN status = 'superseded' THEN status ELSE 'rejected' END,"
        + " updated_at = ?, metadata = CASE WHEN ? IS NULL THEN metadata ELSE json_set(metadata, '$.rejected_reason', ?) END WHERE id = ?",
      ).bind(now(), reason, reason, id),
      this.logOperator(id, "reject", null, reason),
    ]);
  }

  // Purge is the one erase: memory is otherwise never deleted, but a leaked key or a badly wrong
  // lesson must leave every copy kypp reads (row text, FTS, pointers, recall queries). The row stays
  // as a rejected tombstone so its id still resolves, and the log keeps the reason, never the text.
  async operatorPurge(id: string, reason: string): Promise<void> {
    const ts = now();
    await this.db.batch([
      this.db.prepare(
        "UPDATE memory_claims SET subject = ?, content = ?, status = 'rejected', verify = NULL, code_refs = '[]',"
        + " source_ids = '[]', metadata = json_object('purged_at', ?), updated_at = ? WHERE id = ?",
      ).bind(PURGED, PURGED, ts, ts, id),
      this.db.prepare("DELETE FROM claims_fts WHERE claim_id = ?").bind(id),
      this.db.prepare("DELETE FROM code_pointers WHERE claim_id = ?").bind(id),
      // A recall query is copied onto every claim it returned; clear each copy, not just this claim's.
      this.db.prepare("UPDATE claim_usages SET query = NULL WHERE query IN (SELECT query FROM claim_usages WHERE claim_id = ? AND query IS NOT NULL)").bind(id),
      // Earlier free-text reasons (a reject's) may quote the leaked text; the actions and times stay.
      this.db.prepare("UPDATE defrag_log SET reason = NULL WHERE claim_id = ?").bind(id),
      // A feedback note may quote the claim too; the verdict and score stay.
      this.db.prepare("UPDATE claim_usages SET metadata = json_remove(metadata, '$.note') WHERE claim_id = ? AND surface = 'feedback'").bind(id),
      this.logOperator(id, "purge", null, reason),
    ]);
  }

  // One defrag_log row for an operator write, so every human/verified change can be traced.
  // `when` (a query on the claim id) skips the row when the write it logs would not apply.
  private logOperator(id: string, op: string, project: string | null, reason: string | null, when?: string): D1PreparedStatement {
    const sql = "INSERT INTO defrag_log(id, claim_id, action, from_project, to_project, score, reason, created_at)"
      + (when ? ` SELECT ?, ?, ?, NULL, ?, NULL, ?, ? WHERE EXISTS (${when})` : " VALUES(?, ?, ?, NULL, ?, NULL, ?, ?)");
    return this.db.prepare(sql).bind(uid(), id, `operator:${op}`, project, reason, now(), ...(when ? [id] : []));
  }

  // Browse (empty match) = strongest first; otherwise bm25 relevance, then the same tie-breaks as
  // store.recall: accepted, nearer level (project, then its groups, then user, then global), confidence.
  // The id is last so equal rows come back in the same order on every engine.
  async recall(match: string, project: string | null, user: string | null,
    opts: { candidates?: Candidates; groups?: string[]; types?: string[]; agent?: string; limit?: number; session?: string | null; room?: RoomScope } = {}): Promise<Claim[]> {
    match = match.trim();
    const [where, params] = opts.room
      ? roomVisible("c", opts.room, opts.candidates ?? "origin")
      : visible("c", project, opts.groups ?? [], user, opts.candidates ?? "origin", opts.session ?? null);
    const types = opts.types ?? [];
    const [screen, sparams] = this.screen("c", opts.session ?? null);
    let filter = screen + (types.length ? ` AND c.type IN (${types.map(() => "?").join(",")})` : "");
    const fparams: unknown[] = [...sparams, ...types];
    if (opts.agent) { filter += " AND c.agent = ?"; fparams.push(opts.agent); }
    // `groups` comes nearest first, so a parent's claim outranks a grandparent's.
    const groups = opts.room?.groups ?? opts.groups ?? [];
    const near = groups.length ? `1 + (CASE c.project ${groups.map((_, i) => `WHEN ? THEN ${i}`).join(" ")} END) * 0.01` : "1";
    const order = `(c.status='accepted') DESC, CASE c.scope WHEN 'room' THEN 0 WHEN 'project' THEN 0 WHEN 'group' THEN ${near} WHEN 'user' THEN 2 ELSE 3 END, c.confidence DESC, c.updated_at DESC, c.id`;
    const limit = Math.min(Math.max(opts.limit ?? 10, 1), 50);
    // Quote word tokens before MATCH. A path (`kypp/store.py`) is not an FTS expression; `/` is syntax.
    const fts = match ? ftsQuery(match) : "";
    const sql = fts
      ? `SELECT ${claimColumns("c")} FROM claims_fts JOIN memory_claims c ON c.id = claims_fts.claim_id`
        + ` WHERE claims_fts MATCH ? AND ${where}${filter} ORDER BY bm25(claims_fts), ${order} LIMIT ?`
      : `SELECT ${claimColumns("c")} FROM memory_claims c WHERE ${where}${filter} ORDER BY ${order} LIMIT ?`;
    const binds = [...(fts ? [fts] : []), ...params, ...fparams, ...groups, limit];
    const textRead = match && !fts
      ? Promise.resolve({ results: [] as Record<string, unknown>[] })
      : this.db.prepare(sql).bind(...binds).all();
    if (!match) return (await textRead).results.map(hydrate);
    // A path or anchor hit is an identity match. It outranks bm25 and still has to pass visibility.
    const [pointSql, pointBinds] = pointerMatch(match);
    const [{ results }, pointed] = await Promise.all([textRead, this.db.prepare(
      `SELECT ${claimColumns("c")} FROM memory_claims c WHERE c.id IN (`
      + `SELECT claim_id FROM code_pointers WHERE claim_id != '' AND (${pointSql}))`
      + ` AND ${where}${filter} ORDER BY ${order} LIMIT ?`,
    ).bind(...pointBinds, ...params, ...fparams, ...groups, limit).all()]);
    const text = results.map(hydrate);
    const seen = new Set<string>();
    const merged = [];
    for (const c of [...pointed.results.map((r) => ({ ...hydrate(r), pointer: true })), ...text]) {
      if (seen.has(c.id)) continue;
      seen.add(c.id);
      merged.push(c);
      if (merged.length >= limit) break;
    }
    return merged;
  }

  // A read-only copy of what this caller sees (accepted and candidate, every level), paged by id,
  // for an offline cache. Same visibility as recall; nothing here ranks, promotes or writes.
  async snapshot(project: string | null, groups: string[], user: string | null, after: string, limit: number): Promise<Claim[]> {
    const [where, params] = visible("c", project, groups, user, "all", null);
    const [screen, sparams] = this.screen("c", null);
    const { results } = await this.db.prepare(
      `SELECT ${claimColumns("c")} FROM memory_claims c WHERE ${where}${screen} AND c.id > ? ORDER BY c.id LIMIT ?`,
    ).bind(...params, ...sparams, after, limit).all();
    return results.map(hydrate);
  }

  // Code → entries, and entries → code. Line numbers are not stored.
  async linkPointer(entryRef: string, path: string, claimId = "", symbol = "", kind = "context"): Promise<string> {
    const row = pointerRow(claimId, entryRef, path, symbol, kind);
    await this.db.prepare(
      "INSERT OR IGNORE INTO code_pointers(id,claim_id,entry_ref,project,doc_id,anchor,revision,path,symbol,kind,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
    ).bind(...(row as unknown[])).run();
    const found = await this.db.prepare(
      "SELECT id FROM code_pointers WHERE kind = ? AND entry_ref = ? AND path = ? AND symbol = ? AND claim_id = ?",
    ).bind(kind, entryRef, path.replace(LEADING_DOT, ""), symbol, claimId).first();
    return (found?.id as string) ?? (row[0] as string);
  }

  async entriesForPath(path: string): Promise<Record<string, unknown>[]> {
    const { results } = await this.db.prepare(
      "SELECT * FROM code_pointers WHERE path = ? AND path != '' ORDER BY entry_ref",
    ).bind(path.replace(LEADING_DOT, "")).all();
    return results;
  }

  async pathsForEntry(entryRef: string): Promise<Record<string, unknown>[]> {
    const anchor = entryRef.includes("#") ? entryRef.slice(entryRef.indexOf("#") + 1) : entryRef;
    const doc = DOC_REF_WHOLE.exec(entryRef);
    const identity = doc ? "project = ? AND doc_id = ? AND anchor = ?" : "anchor != '' AND anchor = ?";
    const { results } = await this.db.prepare(
      `SELECT * FROM code_pointers WHERE entry_ref = ? OR claim_id = ? OR (${identity}) ORDER BY path`,
    ).bind(entryRef, entryRef, ...(doc ? [doc[1], doc[2], doc[4]] : [anchor])).all();
    return results;
  }

  // A handle is a claim id or its 8+ char prefix; returns any status (a handle may point into history).
  // Another user's user-scope claims are invisible here exactly as in recall, including to the
  // ambiguity check, so a prefix can't probe for them.
  // An unsorted claim resolves only for the session and user that wrote it, as in recall; with no
  // session on either side it resolves for nobody (NULL never matches).
  // A room resolves only its own lane and its linked projects and groups, so a guessed prefix can't
  // reach another repo's or another room's memory.
  async get(handle: string, user: string | null, session: string | null = null, room?: RoomScope): Promise<Claim | null> {
    if (!HANDLE.test(handle)) throw new Error(`bad claim handle ${JSON.stringify(handle)} (expected 8-32 hex chars)`);
    const within = room
      ? ` AND ((scope = 'room' AND user IS ?) OR (scope = 'project' AND project IN (${["", ...room.links].map(() => "?").join(",")}))`
        + ` OR (scope = 'group' AND project IN (${["", ...room.groups].map(() => "?").join(",")})))`
      : "";
    const { results } = await this.db.prepare(
      `SELECT ${claimColumns()} FROM memory_claims WHERE id LIKE ? AND (scope NOT IN ('user','room') OR user IS ?)`
      + " AND NOT (scope = 'project' AND project IS NULL AND NOT (session IS NOT NULL AND session = ? AND user IS ?))" + within + " LIMIT 2",
    ).bind(`${handle}%`, user, session, user, ...(room ? [room.identity, "", ...room.links, "", ...room.groups] : [])).all();
    if (results.length > 1) throw new Error(`ambiguous claim handle ${handle} (use more chars)`);
    return results.length ? hydrate(results[0]) : null;
  }

  // The operator's reach: any claim by handle, whatever its scope or lane (purge must reach a key
  // leaked into a room, another user's scope or an unsorted claim).
  async anyClaim(handle: string): Promise<Claim | null> {
    if (!HANDLE.test(handle)) throw new Error(`bad claim handle ${JSON.stringify(handle)} (expected 8-32 hex chars)`);
    const { results } = await this.db.prepare("SELECT * FROM memory_claims WHERE id LIKE ? LIMIT 2").bind(`${handle}%`).all();
    if (results.length > 1) throw new Error(`ambiguous claim handle ${handle} (use more chars)`);
    return results.length ? hydrate(results[0]) : null;
  }

  // What a read showed, in the order it rendered it: each section's label and claims. Every row keeps
  // where its claim sat (`position` in the whole reply, `section`, `rank` within it), so a rating can
  // be read against what that slot gets anyway: the top of a briefing is used more whatever is there.
  async recordUsage(consumer: string | null, shown: Shown, surface: "recall" | "briefing" | "expand",
    project: string | null, query: string | null = null): Promise<void> {
    const rows = shown.flatMap(([section, claims]) => claims.map((c, i) => ({ c, section, rank: i + 1 })));
    if (!consumer || !rows.length) return;
    const ts = now();
    await this.db.batch(rows.map(({ c, section, rank }, i) => this.db.prepare(
      "INSERT INTO claim_usages(id,consumer,claim_id,project,scope,surface,query,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
    ).bind(uid(), consumer, c.id, project, c.scope, surface, query, JSON.stringify({ position: i + 1, section, rank }), ts)));
  }

  // The shown claim `handle` names for this session: one a read showed it, never a claim it only knows the id of.
  async shownTo(consumer: string, handle: string): Promise<{ id: string; project: string | null; scope: string } | null> {
    if (!HANDLE.test(handle)) throw new Error(`bad claim handle ${JSON.stringify(handle)} (expected 8-32 hex chars)`);
    const { results } = await this.db.prepare(
      "SELECT claim_id AS id, MAX(project) AS project, MAX(scope) AS scope FROM claim_usages"
      + " WHERE consumer = ? AND claim_id LIKE ? AND surface != 'feedback' GROUP BY claim_id LIMIT 2",
    ).bind(consumer, `${handle}%`).all();
    if (results.length > 1) throw new Error(`ambiguous claim handle ${handle} (use more chars)`);
    return results.length ? results[0] as { id: string; project: string | null; scope: string } : null;
  }

  // Each claim's level, for an authorization check on what was shown.
  async lanes(ids: string[]): Promise<Map<string, { scope: string; project: string | null; user: string | null }>> {
    if (!ids.length) return new Map();
    const { results } = await this.db.prepare(
      "SELECT c.id, c.scope, c.project, c.user FROM memory_claims c JOIN json_each(?) j ON j.value = c.id",
    ).bind(JSON.stringify(ids)).all();
    return new Map(results.map((r) => [r.id as string, { scope: r.scope as string, project: r.project as string | null, user: r.user as string | null }]));
  }

  // A session's verdict on claims it was shown: its own usage rows (surface "feedback"), never a change
  // to the claim. The newest row per consumer and claim is the verdict; earlier ones stay as history.
  // One call holds at most one rating per claim (the caller dedupes); rowid breaks created_at ties between calls.
  // Only a rating from a caller that may write at the claim's level is marked `writable`, and only a marked
  // rating confirms a claim; a rating stored before the mark can't show who made it.
  async recordFeedback(consumer: string,
    rated: { id: string; project: string | null; scope: string; score: number; verdict: string; note: string | null; writable: boolean }[],
    room: string | null = null): Promise<void> {
    if (!rated.length) return;
    const ts = now();
    // A purged claim's tombstone is checked in the insert itself, so a note can't bring erased text back.
    // A room's link to the claim's repo is rechecked in the insert too, so an unlink can't race a writable mark.
    await this.db.batch(rated.map((r) => this.db.prepare(
      "INSERT INTO claim_usages(id,consumer,claim_id,project,scope,surface,score,metadata,created_at)"
      + " SELECT ?,?,?,?,?,'feedback',?,CASE WHEN ? = 1 AND (? IS NULL OR c.scope = 'room'"
      + " OR EXISTS (SELECT 1 FROM room_links l WHERE l.room = ? AND l.project = c.project))"
      + " THEN json_set(c.m, '$.writable', json('true')) ELSE c.m END, ?"
      + " FROM (SELECT scope, project, CASE WHEN json_extract(metadata, '$.purged_at') IS NULL THEN ? ELSE ? END AS m"
      + " FROM memory_claims WHERE id = ?) c",
    ).bind(uid(), consumer, r.id, r.project, r.scope, r.score, r.writable ? 1 : 0, room, room, ts,
      JSON.stringify({ verdict: r.verdict, ...(r.note ? { note: r.note } : {}) }), JSON.stringify({ verdict: r.verdict }), r.id)));
  }

  // Per claim, the sessions whose newest rating of it is a writable `used`, with when they rated it.
  async confirmations(ids: string[]): Promise<Map<string, Confirmation[]>> {
    const out = new Map<string, Confirmation[]>();
    if (!ids.length) return out;
    const { results } = await this.db.prepare(
      `SELECT u.claim_id, u.consumer, ${CONFIRMING} AS score, u.created_at FROM claim_usages u JOIN json_each(?) j ON j.value = u.claim_id`
      + " WHERE u.surface = 'feedback' ORDER BY u.created_at, u.rowid",
    ).bind(JSON.stringify(ids)).all();
    // The newest row per (consumer, claim) is that session's verdict; a later not_used or wrong withdraws a used.
    const latest = new Map<string, Record<string, unknown>>();
    for (const r of results) latest.set(`${r.claim_id}\u0000${r.consumer}`, r);
    for (const r of latest.values()) {
      if (r.score !== 1) continue;
      const id = r.claim_id as string;
      out.set(id, [...(out.get(id) ?? []), { session: r.consumer as string, at: r.created_at as string }]);
    }
    return out;
  }

  // Per claim, the newest standing `wrong` and its note. A claim is disputed while some session's newest
  // writable verdict on it is `wrong` and no session's newest writable verdict is a `used` made after it.
  async disputes(ids: string[]): Promise<Map<string, Dispute>> {
    const out = new Map<string, Dispute>();
    if (!ids.length) return out;
    const { results } = await this.db.prepare(
      `SELECT u.claim_id, u.consumer, ${CONFIRMING} AS score, json_extract(u.metadata, '$.note') AS note, u.created_at`
      + " FROM claim_usages u JOIN json_each(?) j ON j.value = u.claim_id WHERE u.surface = 'feedback' ORDER BY u.created_at, u.rowid",
    ).bind(JSON.stringify([...new Set(ids)])).all();
    // Rows arrive in rating order (created_at, then rowid), so the index orders verdicts made in the same millisecond.
    const latest = new Map<string, [Record<string, unknown>, number]>();
    results.forEach((r, i) => latest.set(`${r.claim_id}\u0000${r.consumer}`, [r, i]));
    const used = new Map<string, number>(), wrong = new Map<string, number>();
    for (const [r, i] of latest.values()) {
      const id = r.claim_id as string;
      if (r.score === 1 && i > (used.get(id) ?? -1)) used.set(id, i);
      if (r.score === -1 && i > (wrong.get(id) ?? -1)) {
        wrong.set(id, i);
        out.set(id, { note: (r.note as string | null) ?? null, at: r.created_at as string });
      }
    }
    for (const id of out.keys()) if ((used.get(id) ?? -1) > wrong.get(id)!) out.delete(id);
    return out;
  }

  // Every group or org `project` rolls up to, nearest first, through parents of parents up to three
  // levels (a cycle in the table just stops). The roll-up table is the only thing that decides what a
  // project can see.
  async ancestors(project: string): Promise<string[]> {
    const { results } = await this.db.prepare(
      "WITH RECURSIVE up(name, depth) AS (SELECT parent, 1 FROM project_parents WHERE project = ?"
      + " UNION SELECT p.parent, up.depth + 1 FROM project_parents p JOIN up ON p.project = up.name WHERE up.depth < 3)"
      + " SELECT name, MIN(depth) AS d FROM up WHERE name != ? GROUP BY name ORDER BY d, name",
    ).bind(project, project).all();
    return results.map((r) => r.name as string);
  }

  // The orgs among `groups`: the ones at the top of the roll-up, with no parent of their own.
  async orgs(groups: string[]): Promise<Set<string>> {
    if (!groups.length) return new Set();
    const { results } = await this.db.prepare(
      `SELECT DISTINCT project FROM project_parents WHERE project IN (${groups.map(() => "?").join(",")})`,
    ).bind(...groups).all();
    const nested = new Set(results.map((r) => r.project as string));
    return new Set(groups.filter((g) => !nested.has(g)));
  }

  // The user's own way of working, written by their other sessions and not yet accepted. A process
  // lesson is learned once per repo at most, so waiting for a second session would hide it for long.
  // A room's own lane works the same way (`scope` = "room").
  async recentUserCandidates(user: string, session: string | null, limit: number, scope: "user" | "room" = "user"): Promise<Claim[]> {
    const [screen, sparams] = this.screen("memory_claims", session);
    const { results } = await this.db.prepare(
      `SELECT ${claimColumns()} FROM memory_claims WHERE status = 'candidate' AND scope = ? AND user = ? AND session IS NOT ?`
      + screen + " ORDER BY created_at DESC LIMIT ?",
    ).bind(scope, user, session, ...sparams, limit).all();
    return results.map(hydrate);
  }

  // Other sessions' newest unconfirmed claims in this project, for the briefing's "recent" section.
  async recentCandidates(project: string, session: string | null, limit: number): Promise<Claim[]> {
    const [screen, sparams] = this.screen("memory_claims", session);
    const { results } = await this.db.prepare(
      `SELECT ${claimColumns()} FROM memory_claims WHERE status = 'candidate' AND scope = 'project' AND project = ? AND session IS NOT ?`
      + screen + " ORDER BY created_at DESC LIMIT ?",
    ).bind(project, session, ...sparams, limit).all();
    return results.map(hydrate);
  }

  // Echo guard: was this session shown another session's unconfirmed claim on `subject` in `project`?
  // Repeating what it was shown is not independent evidence, so such a claim doesn't count toward
  // accepting the subject.
  // `at` is the claim's level: a project, or the user's own scope.
  async shownCandidate(session: string, subject: string, at: Lane): Promise<string[]> {
    const [level, key] = laneSql(at);
    const { results } = await this.db.prepare(
      "SELECT DISTINCT c.id FROM claim_usages u JOIN memory_claims c ON c.id = u.claim_id"
      + ` WHERE u.consumer = ? AND c.subject = ? AND ${level} AND c.status = 'candidate'`
      + " AND c.session IS NOT ?",
    ).bind(session, subject, key, session).all();
    return results.map((r) => r.id as string);
  }

  // Other sessions' live subjects in this lane that read like `subject`/`content`, nearest first, for
  // the claim reply. Only subjects are returned and no usage is recorded: the content was written
  // before the agent saw them, so a reuse of one still counts as its own observation.
  // A session shown another session's candidate on this subject doesn't count toward accepting it.
  async nearSubjects(at: Lane, subject: string, content: string, session: string | null, limit: number): Promise<string[]> {
    const fts = ftsQuery(`${subject} ${content}`);
    if (!fts) return [];
    const [level, key] = laneSql(at);
    const [screen, sparams] = this.screen("c", session);
    const { results } = await this.db.prepare(
      "SELECT c.subject FROM claims_fts JOIN memory_claims c ON c.id = claims_fts.claim_id"
      + ` WHERE claims_fts MATCH ? AND ${level} AND c.status IN ('candidate','accepted') AND c.subject != ?`
      + ` AND c.session IS NOT ?${screen} ORDER BY bm25(claims_fts) LIMIT ?`,
    ).bind(fts, key, subject, session, ...sparams, limit * 4).all();
    return [...new Set(results.map((r) => r.subject as string))].slice(0, limit);
  }

  // A session that claims the same content again under another subject is renaming its claim. Read
  // before the new row is written, so a concurrent rename can't pick up the other's replacement.
  async renamedFrom(at: Lane, subject: string, content: string, session: string): Promise<Renamed[]> {
    const [level, key] = laneSql(at);
    const { results } = await this.db.prepare(
      `SELECT c.id, c.subject, c.content, c.source_ids, c.metadata FROM memory_claims c WHERE ${level}`
      + " AND c.status = 'candidate' AND c.session = ? AND c.subject != ?",
    ).bind(key, session, subject).all();
    return results.filter((r) => normalizedContent(r.content as string) === normalizedContent(content)).map((r) => ({
      id: r.id as string, subject: r.subject as string,
      stamped: (JSON.parse((r.source_ids as string) || "[]") as string[]).length > 0,
      echoOf: (JSON.parse((r.metadata as string) || "{}").echo_of ?? []) as string[],
    }));
  }

  // Supersedes renamed claims that are still candidates, pointing at `keep`; returns the subjects it
  // actually withdrew (a pass may have promoted one meanwhile, and that one stays).
  // Change status; never DELETE a claim.
  async withdraw(renamed: Renamed[], keep: string): Promise<string[]> {
    if (!renamed.length) return [];
    const ts = now();
    const results = await this.db.batch(renamed.map((r) => this.db.prepare(
      "UPDATE memory_claims SET status = 'superseded', updated_at = ?, metadata = json_set(metadata, '$.renamed_to', ?)"
      + " WHERE id = ? AND status = 'candidate'",
    ).bind(ts, keep, r.id)));
    return renamed.filter((_, i) => results[i].meta.changes > 0).map((r) => r.subject);
  }

  /** The registered projects a room may read and share into. */
  async roomLinks(key: string): Promise<string[]> {
    const { results } = await this.db.prepare("SELECT project FROM room_links WHERE room = ? ORDER BY project").bind(key).all();
    return results.map((r) => r.project as string);
  }

  async projects(): Promise<ProjectInfo[]> {
    const { results } = await this.db.prepare("SELECT name, description FROM projects ORDER BY name").all();
    return results as unknown as ProjectInfo[];
  }

  // Registers a project the claims can be filed into (the operator's call, never an agent's). False if
  // it was already registered; the description is then left as it was.
  async registerProject(name: string, description: string): Promise<boolean> {
    const r = await this.db.prepare("INSERT OR IGNORE INTO projects(name, description, created_at) VALUES(?,?,?)")
      .bind(name, description, now()).run();
    return r.meta.changes > 0;
  }

  // Unsorted claims whose origin resolves to `name` (`grokbot`, `vu1n/grokbot`), as filing by name would.
  async unsortedFrom(name: string): Promise<Claim[]> {
    const { results } = await this.db.prepare(
      `SELECT * FROM memory_claims WHERE status IN ('candidate','accepted') AND ${UNSORTED} AND instr(origin, ?) > 0`,
    ).bind(name).all();
    return results.map(hydrate).filter((c) => resolveOrigin(c.origin, [name]) === name);
  }


  // Never-tried first, then the ones tried longest ago, so claims the model can't place don't hold
  // up the rest of the queue.
  async unsorted(limit: number): Promise<Claim[]> {
    const { results } = await this.db.prepare(
      `SELECT * FROM memory_claims WHERE status IN ('candidate','accepted') AND ${UNSORTED}`
      + " ORDER BY file_tried_at IS NOT NULL, file_tried_at, created_at LIMIT ?",
    ).bind(limit).all();
    return results.map(hydrate);
  }

  async fileTried(id: string): Promise<void> {
    await this.db.prepare(`UPDATE memory_claims SET file_tried_at = ? WHERE id = ? AND ${UNSORTED}`).bind(now(), id).run();
  }

  // Agent claims the write gate had no verdict on (S1 off, erroring or slow when they were written),
  // never-retried first, so defrag can judge them late.
  async unjudged(limit: number): Promise<Claim[]> {
    const { results } = await this.db.prepare(
      "SELECT * FROM memory_claims WHERE status IN ('candidate','accepted') AND authority = 'agent'"
      + " AND json_extract(metadata, '$.s1') IS NULL"
      + " ORDER BY json_extract(metadata, '$.s1_tried') IS NOT NULL, json_extract(metadata, '$.s1_tried'), created_at LIMIT ?",
    ).bind(limit).all();
    return results.map(hydrate);
  }

  // Records a late verdict, or (verdict null) that the model still had no opinion, for rotation.
  async judged(id: string, verdict: Record<string, unknown> | null): Promise<void> {
    await (verdict
      ? this.db.prepare("UPDATE memory_claims SET metadata = json_set(metadata, '$.s1', json(?)) WHERE id = ?").bind(JSON.stringify(verdict), id)
      : this.db.prepare("UPDATE memory_claims SET metadata = json_set(metadata, '$.s1_tried', ?) WHERE id = ?").bind(now(), id)).run();
  }

  // Change status; never DELETE a claim.
  // Takes a live claim out of memory with the reason in its metadata; bumps updated_at so a plan
  // already made for its group goes stale.
  async reject(id: string, reason: string): Promise<boolean> {
    const r = await this.db.prepare(
      "UPDATE memory_claims SET status = 'rejected', updated_at = ?, metadata = json_set(metadata, '$.rejected', ?)"
      + " WHERE id = ? AND status IN ('candidate','accepted')",
    ).bind(now(), reason, id).run();
    return r.meta.changes > 0;
  }

  // Files one unsorted claim and logs the move. False if it was already filed (a concurrent pass).
  // Why: updated_at is left alone. It dates the lesson, and planGroup treats a newer candidate as an
  // update to an accepted answer; the group guard still notices the move because the count changes.
  async file(id: string, project: string, score: number | null, reason: string): Promise<boolean> {
    const [moved] = await this.db.batch([
      this.db.prepare(`UPDATE memory_claims SET project = ? WHERE id = ? AND ${UNSORTED}`).bind(project, id),
      // The log row is written only for the pass whose update took: one 'file' row per claim.
      this.db.prepare(
        "INSERT INTO defrag_log(id, claim_id, action, from_project, to_project, score, reason, created_at)"
        + " SELECT ?, id, 'file', NULL, project, ?, ?, ? FROM memory_claims WHERE id = ? AND project = ?"
        + " AND NOT EXISTS (SELECT 1 FROM defrag_log WHERE claim_id = ? AND action = 'file')",
      ).bind(uid(), score, reason, now(), id, project, id),
    ]);
    return moved.meta.changes > 0;
  }

  // `filed` leaves out unsorted claims, which must not be grouped or promoted before they have a project.
  async liveClaims(filter: { scope?: Scope; project?: string | null; user?: string | null; subject?: string; filed?: boolean } = {}): Promise<Claim[]> {
    let sql = "SELECT * FROM memory_claims WHERE status IN ('candidate','accepted')";
    const binds: unknown[] = [];
    if (filter.filed) sql += ` AND NOT (${UNSORTED})`;
    if (filter.scope !== undefined) { sql += " AND scope = ?"; binds.push(filter.scope); }
    if (filter.project !== undefined) { sql += " AND project IS ?"; binds.push(filter.project); }
    if (filter.user !== undefined) { sql += " AND user IS ?"; binds.push(filter.user); }
    if (filter.subject !== undefined) { sql += " AND subject = ?"; binds.push(filter.subject); }
    const { results } = await this.db.prepare(sql).bind(...binds).all();
    return results.map(hydrate);
  }

  // Change status; never DELETE a claim.
  // One transaction per group, guarded by the version read at plan time: if the group changed since
  // (a correction, a new claim, a review), the guard's CHECK fails, the group's batch rolls back and
  // the next pass replans it. Returns how many groups were applied and skipped as stale.
  async apply(plans: GroupPlan[]): Promise<{ applied: number; stale: number }> {
    let applied = 0, stale = 0;
    for (const p of plans) {
      const ts = now();
      const { subject, scope, project, user } = p.key;
      const stmts = [
        this.db.prepare(
          "INSERT INTO consolidation_guard(ok) SELECT 0 WHERE (SELECT COUNT(*) || ':' || IFNULL(MAX(updated_at), '')"
          + " FROM memory_claims WHERE status IN ('candidate','accepted') AND subject = ? AND scope = ? AND project IS ?"
          + " AND (scope NOT IN ('user','room') OR user IS ?)) != ?",
        ).bind(subject, scope, project, user, p.version),
        // Count/max-time can repeat after a same-millisecond correction plus a new proposal.
        // Refuse the entire old plan if any target was retired or moved out of its group.
        this.db.prepare(
          "INSERT INTO consolidation_guard(ok) SELECT 0 WHERE EXISTS (SELECT 1 FROM json_each(?) target"
          + " WHERE NOT EXISTS (SELECT 1 FROM memory_claims c WHERE c.id = target.value"
          + " AND c.status IN ('candidate','accepted') AND c.subject = ? AND c.scope = ? AND c.project IS ?"
          + " AND (c.scope NOT IN ('user','room') OR c.user IS ?)))",
        ).bind(JSON.stringify([...p.supersede, ...p.promote]), subject, scope, project, user),
        // A `used` rating the promotion counted must still be that session's verdict, at the time the plan counted
        // (recurrence is timed by it): a later rating doesn't touch the claim rows the version guard reads.
        ...(p.confirmed?.length ? [this.db.prepare(
          `INSERT INTO consolidation_guard(ok) SELECT 0 WHERE EXISTS (SELECT 1 FROM json_each(?) t WHERE IFNULL((SELECT CASE WHEN u.created_at = json_extract(t.value, '$.at') THEN ${CONFIRMING} END FROM claim_usages u`
          + ` WHERE u.surface = 'feedback' AND u.claim_id = json_extract(t.value, '$.claim') AND u.consumer = json_extract(t.value, '$.session')`
          + " ORDER BY u.created_at DESC, u.rowid DESC LIMIT 1), 0) != 1)",
        ).bind(JSON.stringify(p.confirmed))] : []),
        ...p.supersede.map((id) => this.db.prepare("UPDATE memory_claims SET status='superseded', updated_at=? WHERE id=?").bind(ts, id)),
        ...p.promote.map((id) => this.db.prepare("UPDATE memory_claims SET status='accepted', updated_at=? WHERE id=?").bind(ts, id)),
      ];
      try {
        await this.db.batch(stmts);
        applied++;
      } catch (e) {
        if (!String((e as Error).message).includes("CHECK constraint failed")) throw e;
        stale++;
      }
    }
    return { applied, stale };
  }
}

async function sha256(value: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
