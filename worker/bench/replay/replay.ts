// replay.ts — a bench-only Worker that drives the production D1Store against whatever D1 binding it
// is given (workerd's local SQLite under `wrangler dev`, or a real D1 with `remote: true`), so the
// same code can be replayed against both storage engines. Not deployed; not part of src/.
import { D1Store } from "../../src/store.ts";
import { ftsQuery } from "../../src/memory.ts";

interface Env { DB: D1Database }
type Seed = { id: string; type: string; subject: string; content: string; scope: string; project: string | null;
  status: string; confidence: number; ts: string };

const json = (v: unknown) => new Response(JSON.stringify(v), { headers: { "content-type": "application/json" } });

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/seed") {
      // Same columns D1Store.claim writes, with fixed ids and timestamps so every engine holds identical rows.
      const { claims } = await req.json() as { claims: Seed[] };
      await env.DB.batch(claims.flatMap((c) => [
        env.DB.prepare(
          "INSERT OR IGNORE INTO memory_claims(id,type,subject,content,scope,project,origin,session,agent,user,status,authority,confidence,source_ids,code_refs,metadata,created_at,updated_at)"
          + " VALUES(?,?,?,?,?,?,?,NULL,'replay',NULL,?,'agent',?,'[]','[]','{}',?,?)",
        ).bind(c.id, c.type, c.subject, c.content, c.scope, c.project, c.project, c.status, c.confidence, c.ts, c.ts),
        env.DB.prepare("INSERT INTO claims_fts(claim_id, subject, content) SELECT ?,?,? WHERE changes() > 0").bind(c.id, c.subject, c.content),
      ]));
      return json({ ok: claims.length });
    }
    if (url.pathname === "/count") {
      const r = await env.DB.prepare("SELECT (SELECT COUNT(*) FROM memory_claims) AS claims, (SELECT COUNT(*) FROM claims_fts) AS fts").first();
      return json(r);
    }
    if (url.pathname === "/recall") {
      const { q, project, limit, candidates } = await req.json() as { q: string; project: string; limit: number; candidates?: "none" | "origin" };
      const t0 = performance.now();
      const claims = await new D1Store(env.DB).recall(ftsQuery(q), project, null, { candidates: candidates ?? "none", groups: [], limit });
      return json({ ids: claims.map((c) => c.id), ms: performance.now() - t0 });
    }
    return new Response("not found", { status: 404 });
  },
};
