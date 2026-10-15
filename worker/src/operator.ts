// operator.ts — POST /v1/operator: the operator's `kypp` CLI against the hosted store (KYPP_URL).
// One bearer secret, KYPP_OPERATOR_TOKEN, apart from OAuth grants, KYPP_API_TOKENS and the S1 tokens.
// Context: doc://kypp/authority-order@0007#authority-dominates — kypp correct/verify write human and verified only here; unset token = 404, never on /mcp, every write in defrag_log.
import { type Caller, type ToolCtx, callTool } from "./mcp.ts";
import { DEFAULT_POLICY, HUMAN_CORRECTION_CONFIDENCE, type Policy, type Scope, resolveOrigin } from "./memory.ts";
import { consolidate } from "./consolidate.ts";
import type { S1Client } from "./s1.ts";
import { sameSecret } from "./secret.ts";
import { D1Store } from "./store.ts";

export const OPERATOR_PATH = "/v1/operator";
// The operator is the deployment's owner: the same user the passphrase sign-in stamps, so the CLI and
// the owner's MCP clients share one user scope.
const OPERATOR_USER = "owner";

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
      const id = await store.operatorClaim({
        type: a.type ?? "fact", subject, content: str(a.content), scope, project: where,
        confidence: a.confidence ?? (human ? HUMAN_CORRECTION_CONFIDENCE : 0.7), sourceIds: [], codeRefs: [],
        accept: !!a.accept, agent: caller.agent, user: OPERATOR_USER,
      }, human ? "human" : "agent", str(a.verify) || null, a.op);
      if (!human) return { id, status: (await store.get(id, OPERATOR_USER))!.status };
      // As kypp correct: the new human claim supersedes the subject's other live claims.
      const filter = { scope, project: where, subject, ...(scope === "user" ? { user: OPERATOR_USER } : {}) };
      const before = (await store.liveClaims(filter)).filter((c) => c.id !== id).length;
      await consolidate(store, opts.policy ?? DEFAULT_POLICY, opts.s1 ?? null, filter);
      const after = (await store.liveClaims(filter)).filter((c) => c.id !== id).length;
      return { id, superseded: before - after };
    }
    case "reject": {
      const c = await store.get(str(a.handle), OPERATOR_USER);
      if (!c) throw new Error(`no claim ${JSON.stringify(a.handle)}`);
      await store.operatorReject(c.id, str(a.reason) || null);
      return { claim: await store.get(c.id, OPERATOR_USER) };
    }
    case "verifiable": {
      const where = await project();
      if (!where) throw new Error(`unknown project ${JSON.stringify(a.project)}`);
      return { claims: (await store.verifiable(where, OPERATOR_USER, str(a.subject) || undefined)).map(({ id, subject, verify }) => ({ id, subject, verify })) };
    }
    case "mark_verified": {
      if (typeof a.passed !== "boolean") throw new Error("passed must be true or false");
      if (!(await store.markVerified(str(a.id), a.passed))) throw new Error(`no verifiable claim ${JSON.stringify(a.id)}`);
      return { ok: true };
    }
  }
  throw new Error(`unknown op ${JSON.stringify(a.op)}`);
}
