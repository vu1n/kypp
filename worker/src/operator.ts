// operator.ts — POST /v1/operator: the operator's `kypp` CLI against the hosted store (KYPP_URL).
// One bearer secret, KYPP_OPERATOR_TOKEN, apart from OAuth grants, KYPP_API_TOKENS and the S1 tokens.
// kypp correct/verify write human and verified only here; unset token = 404, never on /mcp, every write in defrag_log.
import { type Caller, type ToolCtx, callTool } from "./mcp.ts";
import { DEFAULT_POLICY, HUMAN_CORRECTION_CONFIDENCE, type Policy, type Scope, resolveOrigin } from "./memory.ts";
import type { S1Client } from "./s1.ts";
import { sameSecret } from "./secret.ts";
import { D1Store } from "./store.ts";

export const OPERATOR_PATH = "/v1/operator";
// The operator is the deployment's owner: the same user the passphrase sign-in stamps, so the CLI and
// the owner's MCP clients share one user scope.
const OPERATOR_USER = "owner";
// One snapshot page stays well under D1 and Worker response limits (claims cap content at 4000 chars).
const SNAPSHOT_PAGE = 500;
// A repo's bare name, as `resolveOrigin` matches the last segment of an origin like `owner/repo`.
const PROJECT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
// An idempotency key the client makes once per write and sends again on every retry of it.
const WRITE_KEY = /^[A-Za-z0-9_-]{16,128}$/;

type Args = Record<string, any>;
export interface OperatorOptions { s1?: S1Client | null; policy?: Policy }

const json = (body: unknown, status = 200) => Response.json(body, { status });
const str = (v: unknown) => (typeof v === "string" ? v : "");

export async function handleOperator(request: Request, db: D1Database, secret: string | undefined, opts: OperatorOptions = {}): Promise<Response> {
  // Unset means the route doesn't exist, so a deployment that never opted in exposes nothing.
  if (!secret) return new Response("not found", { status: 404 });
  const token = (request.headers.get("Authorization") ?? "").match(/^Bearer (.+)$/)?.[1] ?? "";
  if (!token || !(await sameSecret(token, secret))) return json({ error: "operator token required" }, 401);
  if (request.method !== "POST") return new Response("POST JSON to this endpoint", { status: 405, headers: { Allow: "POST" } });
  let a: Args;
  try {
    a = await request.json();
  } catch {
    return json({ error: "body must be JSON" }, 400);
  }
  try {
    return json(await run(new D1Store(db), a, opts));
  } catch (e) {
    return json({ error: String((e as Error).message ?? e) }, 400);
  }
}

async function run(store: D1Store, a: Args, opts: OperatorOptions): Promise<unknown> {
  const caller: Caller = { user: OPERATOR_USER, agent: str(a.agent) || "kypp-cli" };
  const ctx: ToolCtx = { project: null, session: null, caller, s1: opts.s1 ?? null, policy: opts.policy ?? DEFAULT_POLICY, correct: true };
  const project = async () => resolveOrigin(str(a.project), (await store.projects()).map((p) => p.name));
  switch (a.op) {
    // Reads render exactly like the MCP tools, so `kypp recall` over KYPP_URL reads as an agent's recall does.
    case "recall":
      return { text: await callTool(store, "recall", { query: str(a.query), project: a.project, types: a.types, include_candidates: !!a.include_candidates, limit: a.limit }, ctx) };
    case "briefing":
      return { text: await callTool(store, "briefing", { project: a.project, limit: a.limit }, ctx) };
    case "show": {
      const c = await store.get(str(a.handle), OPERATOR_USER);
      if (!c) throw new Error(`no claim ${JSON.stringify(a.handle)}`);
      return { claim: c };
    }
    case "remember":
    case "correct": {
      const human = a.op === "correct";
      const scope: Scope = a.scope ?? "project";
      const where = scope === "project" ? await project() : null;
      if (scope === "project" && !where) throw new Error(`unknown project ${JSON.stringify(a.project)}; register it before writing project memory`);
      const subject = str(a.subject);
      if (a.key != null && !(typeof a.key === "string" && WRITE_KEY.test(a.key))) throw new Error("key must be 16-128 letters, digits, '_' or '-'");
      const key = a.key ?? null;
      const { id, superseded, replayed } = await store.operatorClaim({
        type: a.type ?? "fact", subject, content: str(a.content), scope, project: where,
        confidence: a.confidence ?? (human ? HUMAN_CORRECTION_CONFIDENCE : 0.7), sourceIds: [], codeRefs: [],
        accept: !!a.accept, agent: caller.agent, user: OPERATOR_USER,
      }, human ? "correct" : "remember", str(a.verify) || null, key);
      // A replayed claim may since have been purged or moved out of the operator's view; report what it is now.
      const status = (await store.anyClaim(id))?.status ?? "gone";
      if (!human) return { id, status, ...(key ? { key, replayed } : {}) };
      return { id, superseded, ...(key ? { key, replayed, status } : {}) };
    }
    case "reject": {
      const c = await store.get(str(a.handle), OPERATOR_USER);
      if (!c) throw new Error(`no claim ${JSON.stringify(a.handle)}`);
      await store.operatorReject(c.id, str(a.reason) || null);
      return { claim: await store.get(c.id, OPERATOR_USER) };
    }
    case "purge": {
      const reason = str(a.reason).trim();
      if (!reason) throw new Error("purge needs a reason (it is logged; don't paste the purged text into it)");
      const c = await store.anyClaim(str(a.handle));
      if (!c) throw new Error(`no claim ${JSON.stringify(a.handle)}`);
      await store.operatorPurge(c.id, reason);
      return { claim: await store.anyClaim(c.id) };
    }
    case "verifiable": {
      const where = await project();
      if (!where) throw new Error(`unknown project ${JSON.stringify(a.project)}`);
      return { claims: (await store.verifiable(where, OPERATOR_USER, str(a.subject) || undefined)).map(({ id, subject, verify }) => ({ id, subject, verify })) };
    }
    // `kypp sync`'s pull: the operator's view of one project, a page at a time (id order, `after` cursor).
    case "snapshot": {
      const where = await project();
      if (!where) throw new Error(`unknown project ${JSON.stringify(a.project)}`);
      const limit = Math.min(Math.max(Number(a.limit) || SNAPSHOT_PAGE, 1), SNAPSHOT_PAGE);
      const claims = await store.snapshot(where, await store.ancestors(where), OPERATOR_USER, str(a.after), limit);
      return { project: where, claims, next: claims.length === limit ? claims[claims.length - 1].id : null };
    }
    // What this Worker supports, so a newer CLI doesn't trust a feature an older deployment ignores.
    case "capabilities":
      return { idempotent_writes: true };
    case "projects":
      return { projects: await store.projects() };
    // Filing puts an unsorted claim into a known project; origin is kept, the move is logged, and an unsure claim stays unsorted.
    // Why: rows come only from the operator (an unknown name in an agent's call never creates one), and
    // claims already waiting under that origin are filed now rather than over the next hourly passes.
    case "register": {
      const name = str(a.project).trim();
      if (!PROJECT_NAME.test(name)) throw new Error(`bad project name ${JSON.stringify(a.project)}: use the repo's name, letters, digits, '.', '_' or '-'`);
      const created = await store.registerProject(name, str(a.description));
      let filed = 0;
      for (const c of await store.unsortedFrom(name)) {
        if (await store.file(c.id, name, null, "origin matches a registered project")) filed++;
      }
      return { project: name, created, filed };
    }
    case "mark_verified": {
      if (typeof a.passed !== "boolean") throw new Error("passed must be true or false");
      if (!(await store.markVerified(str(a.id), a.passed))) throw new Error(`no verifiable claim ${JSON.stringify(a.id)}`);
      return { ok: true };
    }
  }
  throw new Error(`unknown op ${JSON.stringify(a.op)}`);
}
