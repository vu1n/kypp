// store.ts — the D1 side of kypp/store.py: claim, recall, get, usage, live claims, status writes.
import { type Claim, type ClaimType, type Plan, SCOPES, type Scope, TYPES } from "./memory.ts";

const MAX_CONTENT = 4000;
const now = () => new Date().toISOString();
const uid = () => crypto.randomUUID().replaceAll("-", "");

function hydrate(r: Record<string, unknown>): Claim {
  return {
    ...(r as unknown as Claim),
    source_ids: JSON.parse((r.source_ids as string) || "[]"),
    code_refs: JSON.parse((r.code_refs as string) || "[]"),
  };
}

// What a caller sees: this project's scope, their own user scope, and the global scope; never
// superseded/rejected history. `t` is the table alias.
function visible(t: string, project: string | null, user: string | null, includeCandidates: boolean): [string, unknown[]] {
  const status = includeCandidates ? `${t}.status IN ('candidate','accepted')` : `${t}.status = 'accepted'`;
  const scopes = [`${t}.scope = 'global'`];
  const params: unknown[] = [];
  if (project) { scopes.push(`(${t}.scope = 'project' AND ${t}.project = ?)`); params.push(project); }
  if (user) { scopes.push(`(${t}.scope = 'user' AND ${t}.user = ?)`); params.push(user); }
  return [`${status} AND (${scopes.join(" OR ")})`, params];
}

export interface ClaimInput {
  type: ClaimType;
  subject: string;
  content: string;
  scope: Scope;
  project: string | null;
  confidence: number;
  sourceIds: string[];
  codeRefs: Record<string, unknown>[];
  accept: boolean;
  agent: string | null;
  user: string | null;
  metadata?: Record<string, unknown>;
}

export class D1Store {
  constructor(private db: D1Database) {}

  // Context: doc://kypp/authority-order@0003#authority-dominates — the Worker only ever writes agent authority; human comes from the operator's local `kypp correct`.
  async claim(c: ClaimInput): Promise<string> {
    if (!TYPES.includes(c.type)) throw new Error(`bad type ${c.type}`);
    if (!SCOPES.includes(c.scope)) throw new Error(`bad scope ${c.scope}`);
    if (c.scope === "project" && !c.project) throw new Error("a project-scoped claim needs a project");
    if (c.scope === "user" && !c.user) throw new Error("a user-scoped claim needs a signed-in user");
    if (!c.subject.trim() || !c.content.trim()) throw new Error("subject and content are required");
    if (c.content.length > MAX_CONTENT) throw new Error(`content over ${MAX_CONTENT} chars; distill it`);
    const id = uid(), ts = now();
    const confidence = Math.min(1, Math.max(0, c.confidence));
    await this.db.batch([
      this.db.prepare(
        "INSERT INTO memory_claims(id,type,subject,content,scope,project,agent,user,status,authority,confidence,source_ids,code_refs,metadata,created_at,updated_at)"
        + " VALUES(?,?,?,?,?,?,?,?,?,'agent',?,?,?,?,?,?)",
      ).bind(id, c.type, c.subject, c.content, c.scope, c.scope === "project" ? c.project : null, c.agent, c.user,
        c.accept ? "accepted" : "candidate", confidence, JSON.stringify(c.sourceIds), JSON.stringify(c.codeRefs),
        JSON.stringify(c.metadata ?? {}), ts, ts),
      this.db.prepare("INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)").bind(id, c.subject, c.content),
    ]);
    return id;
  }

  // Browse (empty match) = strongest first; otherwise bm25 relevance, then the same tie-breaks as
  // store.recall: accepted, nearer scope (project, then user, then global), confidence.
  async recall(match: string, project: string | null, user: string | null,
    opts: { includeCandidates?: boolean; types?: string[]; agent?: string; limit?: number } = {}): Promise<Claim[]> {
    const [where, params] = visible("c", project, user, !!opts.includeCandidates);
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

  // A handle is a claim id or its prefix; returns any status (a handle may point into history).
  async get(handle: string): Promise<Claim | null> {
    if (!/^[0-9a-f]{4,32}$/.test(handle)) throw new Error(`bad claim handle ${JSON.stringify(handle)} (expected 4-32 hex chars)`);
    const { results } = await this.db.prepare("SELECT * FROM memory_claims WHERE id LIKE ? LIMIT 2").bind(`${handle}%`).all();
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

  async liveClaims(filter: { scope?: Scope; project?: string | null; user?: string | null; subject?: string } = {}): Promise<Claim[]> {
    let sql = "SELECT * FROM memory_claims WHERE status IN ('candidate','accepted')";
    const binds: unknown[] = [];
    if (filter.scope !== undefined) { sql += " AND scope = ?"; binds.push(filter.scope); }
    if (filter.project !== undefined) { sql += " AND project IS ?"; binds.push(filter.project); }
    if (filter.user !== undefined) { sql += " AND user IS ?"; binds.push(filter.user); }
    if (filter.subject !== undefined) { sql += " AND subject = ?"; binds.push(filter.subject); }
    const { results } = await this.db.prepare(sql).bind(...binds).all();
    return results.map(hydrate);
  }

  // Context: doc://kypp/append-only-history@0001#never-delete — change status; never DELETE a claim.
  async apply(plan: Plan): Promise<void> {
    const ts = now();
    const stmts = [
      ...plan.supersede.map((id) => this.db.prepare("UPDATE memory_claims SET status='superseded', updated_at=? WHERE id=?").bind(ts, id)),
      ...plan.promote.map((id) => this.db.prepare("UPDATE memory_claims SET status='accepted', updated_at=? WHERE id=?").bind(ts, id)),
    ];
    if (stmts.length) await this.db.batch(stmts);
  }
}
