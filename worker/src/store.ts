// store.ts — the D1 side of kypp/store.py: claim, recall, get, usage, live claims, status writes.
import { type Claim, type ClaimType, type GroupPlan, SCOPES, type Scope, TYPES } from "./memory.ts";
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

function hydrate(r: Record<string, unknown>): Claim {
  return {
    ...(r as unknown as Claim),
    source_ids: JSON.parse((r.source_ids as string) || "[]"),
    code_refs: JSON.parse((r.code_refs as string) || "[]"),
  };
}

const UNSORTED = "scope = 'project' AND project IS NULL";

// Which unconfirmed (candidate) claims a read returns besides accepted memory: none (briefing),
// other agents' claims in the caller's own project (the default), or candidates at every level.
export type Candidates = "none" | "origin" | "all";

// What a caller sees: this project, the groups it rolls up to, their own user scope, the global
// scope, and the unsorted claims their own session wrote; never superseded/rejected history.
// `t` is the table alias.
// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — reads cover every level the caller rolls up to; other sessions' candidates show only in the caller's own project.
// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — an unsorted claim is visible only to the session (and user) that wrote it until it is filed.
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
}

export class D1Store {
  private db: D1Database;
  constructor(db: D1Database) { this.db = db; }

  // Context: doc://kypp/authority-order@0003#authority-dominates — the Worker only ever writes agent authority; human comes from the operator's local `kypp correct`.
  async claim(c: ClaimInput): Promise<string> {
    if (!TYPES.includes(c.type)) throw new Error(`bad type ${c.type}`);
    if (!SCOPES.includes(c.scope)) throw new Error(`bad scope ${c.scope}`);
    if (c.scope === "user" && !c.user) throw new Error("a user-scoped claim needs a signed-in user");
    if (!c.subject.trim() || !c.content.trim()) throw new Error("subject and content are required");
    if (c.content.length > MAX_CONTENT) throw new Error(`content over ${MAX_CONTENT} chars; distill it`);
    const id = uid(), ts = now();
    const confidence = Math.min(1, Math.max(0, Number(c.confidence) || 0));
    const codeRefs = cleanCodeRefs(c.codeRefs);
    await this.db.batch([
      this.db.prepare(
        "INSERT INTO memory_claims(id,type,subject,content,scope,project,origin,session,agent,user,status,authority,confidence,source_ids,code_refs,metadata,created_at,updated_at)"
        + " VALUES(?,?,?,?,?,?,?,?,?,?,?,'agent',?,?,?,?,?,?)",
      ).bind(id, c.type, c.subject, c.content, c.scope, c.scope === "project" ? c.project : null, c.origin ?? c.project, c.session ?? null, c.agent, c.user,
        c.accept ? "accepted" : "candidate", confidence, JSON.stringify(c.sourceIds), JSON.stringify(codeRefs),
        JSON.stringify(c.metadata ?? {}), ts, ts),
      this.db.prepare("INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)").bind(id, c.subject, c.content),
    ]);
    return id;
  }

  // Browse (empty match) = strongest first; otherwise bm25 relevance, then the same tie-breaks as
  // store.recall: accepted, nearer level (project, then its groups, then user, then global), confidence.
  async recall(match: string, project: string | null, user: string | null,
    opts: { candidates?: Candidates; groups?: string[]; types?: string[]; agent?: string; limit?: number; session?: string | null } = {}): Promise<Claim[]> {
    const [where, params] = visible("c", project, opts.groups ?? [], user, opts.candidates ?? "origin", opts.session ?? null);
    const types = opts.types ?? [];
    let filter = types.length ? ` AND c.type IN (${types.map(() => "?").join(",")})` : "";
    const fparams: unknown[] = [...types];
    if (opts.agent) { filter += " AND c.agent = ?"; fparams.push(opts.agent); }
    // `groups` comes nearest first, so a parent's claim outranks a grandparent's.
    const groups = opts.groups ?? [];
    const near = groups.length ? `1 + (CASE c.project ${groups.map((_, i) => `WHEN ? THEN ${i}`).join(" ")} END) * 0.01` : "1";
    const order = `(c.status='accepted') DESC, CASE c.scope WHEN 'project' THEN 0 WHEN 'group' THEN ${near} WHEN 'user' THEN 2 ELSE 3 END, c.confidence DESC, c.updated_at DESC`;
    const limit = Math.min(Math.max(opts.limit ?? 10, 1), 50);
    const sql = match
      ? `SELECT c.* FROM claims_fts JOIN memory_claims c ON c.id = claims_fts.claim_id`
        + ` WHERE claims_fts MATCH ? AND ${where}${filter} ORDER BY bm25(claims_fts), ${order} LIMIT ?`
      : `SELECT c.* FROM memory_claims c WHERE ${where}${filter} ORDER BY ${order} LIMIT ?`;
    const binds = [...(match ? [match] : []), ...params, ...fparams, ...groups, limit];
    const { results } = await this.db.prepare(sql).bind(...binds).all();
    return results.map(hydrate);
  }

  // A handle is a claim id or its 8+ char prefix; returns any status (a handle may point into history).
  // Another user's user-scope claims are invisible here exactly as in recall, including to the
  // ambiguity check, so a prefix can't probe for them.
  // An unsorted claim resolves only for the session and user that wrote it, as in recall; with no
  // session on either side it resolves for nobody (NULL never matches).
  async get(handle: string, user: string | null, session: string | null = null): Promise<Claim | null> {
    if (!/^[0-9a-f]{8,32}$/.test(handle)) throw new Error(`bad claim handle ${JSON.stringify(handle)} (expected 8-32 hex chars)`);
    const { results } = await this.db.prepare(
      "SELECT * FROM memory_claims WHERE id LIKE ? AND (scope != 'user' OR user IS ?)"
      + " AND NOT (scope = 'project' AND project IS NULL AND NOT (session IS NOT NULL AND session = ? AND user IS ?)) LIMIT 2",
    ).bind(`${handle}%`, user, session, user).all();
    if (results.length > 1) throw new Error(`ambiguous claim handle ${handle} (use more chars)`);
    return results.length ? hydrate(results[0]) : null;
  }

  async recordUsage(consumer: string | null, claims: Claim[], surface: "recall" | "briefing" | "expand",
    project: string | null, query: string | null = null): Promise<void> {
    if (!consumer || !claims.length) return;
    const ts = now();
    await this.db.batch(claims.map((c) => this.db.prepare(
      "INSERT INTO claim_usages(id,consumer,claim_id,project,scope,surface,query,created_at) VALUES(?,?,?,?,?,?,?,?)",
    ).bind(uid(), consumer, c.id, project, c.scope, surface, query, ts)));
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

  // Other sessions' newest unconfirmed claims in this project, for the briefing's "recent" section.
  async recentCandidates(project: string, session: string | null, limit: number): Promise<Claim[]> {
    const { results } = await this.db.prepare(
      "SELECT * FROM memory_claims WHERE status = 'candidate' AND scope = 'project' AND project = ? AND session IS NOT ?"
      + " ORDER BY created_at DESC LIMIT ?",
    ).bind(project, session, limit).all();
    return results.map(hydrate);
  }

  // Echo guard: was this session shown another session's unconfirmed claim on `subject` in `project`?
  // Repeating what it was shown is not independent evidence, so such a claim doesn't count toward
  // accepting the subject.
  async shownCandidate(session: string, subject: string, project: string): Promise<string[]> {
    const { results } = await this.db.prepare(
      "SELECT DISTINCT c.id FROM claim_usages u JOIN memory_claims c ON c.id = u.claim_id"
      + " WHERE u.consumer = ? AND c.subject = ? AND c.scope = 'project' AND c.project = ? AND c.status = 'candidate'"
      + " AND c.session IS NOT ?",
    ).bind(session, subject, project, session).all();
    return results.map((r) => r.id as string);
  }

  async projects(): Promise<ProjectInfo[]> {
    const { results } = await this.db.prepare("SELECT name, description FROM projects ORDER BY name").all();
    return results as unknown as ProjectInfo[];
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

  // Context: doc://kypp/append-only-history@0001#never-delete — change status; never DELETE a claim.
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

  // Context: doc://kypp/append-only-history@0001#never-delete — change status; never DELETE a claim.
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
          + " AND (scope != 'user' OR user IS ?)) != ?",
        ).bind(subject, scope, project, user, p.version),
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
