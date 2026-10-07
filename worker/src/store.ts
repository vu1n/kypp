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

// What a caller sees: this project's scope, their own user scope, the global scope, and the unsorted
// claims their own session wrote; never superseded/rejected history. `t` is the table alias.
function visible(t: string, project: string | null, user: string | null, includeCandidates: boolean, session: string | null): [string, unknown[]] {
  const status = includeCandidates ? `${t}.status IN ('candidate','accepted')` : `${t}.status = 'accepted'`;
  const scopes = [`${t}.scope = 'global'`];
  const params: unknown[] = [];
  if (project) { scopes.push(`(${t}.scope = 'project' AND ${t}.project = ?)`); params.push(project); }
  if (user) { scopes.push(`(${t}.scope = 'user' AND ${t}.user = ?)`); params.push(user); }
  if (session) { scopes.push(`(${t}.scope = 'project' AND ${t}.project IS NULL AND ${t}.session = ?)`); params.push(session); }
  return [`${status} AND (${scopes.join(" OR ")})`, params];
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
  // store.recall: accepted, nearer scope (project, then user, then global), confidence.
  async recall(match: string, project: string | null, user: string | null,
    opts: { includeCandidates?: boolean; types?: string[]; agent?: string; limit?: number; session?: string | null } = {}): Promise<Claim[]> {
    const [where, params] = visible("c", project, user, !!opts.includeCandidates, opts.session ?? null);
    const types = opts.types ?? [];
    let filter = types.length ? ` AND c.type IN (${types.map(() => "?").join(",")})` : "";
    const fparams: unknown[] = [...types];
    if (opts.agent) { filter += " AND c.agent = ?"; fparams.push(opts.agent); }
    const order = "(c.status='accepted') DESC, CASE c.scope WHEN 'project' THEN 0 WHEN 'user' THEN 1 ELSE 2 END, c.confidence DESC, c.updated_at DESC";
    const limit = Math.min(Math.max(opts.limit ?? 10, 1), 50);
    const sql = match
      ? `SELECT c.* FROM claims_fts JOIN memory_claims c ON c.id = claims_fts.claim_id`
        + ` WHERE claims_fts MATCH ? AND ${where}${filter} ORDER BY bm25(claims_fts), ${order} LIMIT ?`
      : `SELECT c.* FROM memory_claims c WHERE ${where}${filter} ORDER BY ${order} LIMIT ?`;
    const binds = [...(match ? [match] : []), ...params, ...fparams, limit];
    const { results } = await this.db.prepare(sql).bind(...binds).all();
    return results.map(hydrate);
  }

  // A handle is a claim id or its 8+ char prefix; returns any status (a handle may point into history).
  // Another user's user-scope claims are invisible here exactly as in recall, including to the
  // ambiguity check, so a prefix can't probe for them.
  async get(handle: string, user: string | null): Promise<Claim | null> {
    if (!/^[0-9a-f]{8,32}$/.test(handle)) throw new Error(`bad claim handle ${JSON.stringify(handle)} (expected 8-32 hex chars)`);
    const { results } = await this.db.prepare(
      "SELECT * FROM memory_claims WHERE id LIKE ? AND (scope != 'user' OR user IS ?) LIMIT 2",
    ).bind(`${handle}%`, user).all();
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

  async projects(): Promise<ProjectInfo[]> {
    const { results } = await this.db.prepare("SELECT name, description FROM projects ORDER BY name").all();
    return results as unknown as ProjectInfo[];
  }

  async unsorted(limit = 50): Promise<Claim[]> {
    const { results } = await this.db.prepare(
      `SELECT * FROM memory_claims WHERE status IN ('candidate','accepted') AND ${UNSORTED} ORDER BY created_at LIMIT ?`,
    ).bind(limit).all();
    return results.map(hydrate);
  }

  // Files one unsorted claim and logs the move. False if it was already filed (a concurrent pass).
  async file(id: string, project: string, score: number | null, reason: string): Promise<boolean> {
    const ts = now();
    // The log row is written only if the update just above it took.
    const [moved] = await this.db.batch([
      this.db.prepare(`UPDATE memory_claims SET project = ?, updated_at = ? WHERE id = ? AND ${UNSORTED}`).bind(project, ts, id),
      this.db.prepare(
        "INSERT INTO defrag_log(id, claim_id, action, from_project, to_project, score, reason, created_at)"
        + " SELECT ?, id, 'file', NULL, project, ?, ?, ? FROM memory_claims WHERE id = ? AND project = ? AND updated_at = ?",
      ).bind(uid(), score, reason, ts, id, project, ts),
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
