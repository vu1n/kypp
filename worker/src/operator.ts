// operator.ts — POST /v1/operator: the operator's `kypp` CLI against the hosted store (KYPP_URL).
// One bearer secret, KYPP_OPERATOR_TOKEN, apart from OAuth grants, KYPP_API_TOKENS and the S1 tokens.
// Context: doc://kypp/authority-order@0008#authority-dominates — kypp correct/verify write human and verified only here; unset token = 404, never on /mcp, every write in defrag_log.
import { type Caller, type ToolCtx, callTool } from "./mcp.ts";
import { DEFAULT_POLICY, HUMAN_CORRECTION_CONFIDENCE, type Policy, type Scope, resolveOrigin } from "./memory.ts";
import type { S1Client } from "./s1.ts";
import { sameSecret } from "./secret.ts";
import { D1Store } from "./store.ts";

export const OPERATOR_PATH = "/v1/operator";
// The operator is the deployment's owner: the same user the passphrase sign-in stamps, so the CLI and
// the owner's MCP clients share one user scope.
const OPERATOR_USER = "owner";
// A repo's bare name, as `resolveOrigin` matches the last segment of an origin like `owner/repo`.
const PROJECT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

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
      const { id, superseded } = await store.operatorClaim({
        type: a.type ?? "fact", subject, content: str(a.content), scope, project: where,
        confidence: a.confidence ?? (human ? HUMAN_CORRECTION_CONFIDENCE : 0.7), sourceIds: [], codeRefs: [],
        accept: !!a.accept, agent: caller.agent, user: OPERATOR_USER,
      }, human ? "correct" : "remember", str(a.verify) || null);
      if (!human) return { id, status: (await store.get(id, OPERATOR_USER))!.status };
      return { id, superseded };
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
    case "projects":
      return { projects: await store.projects() };
    // Context: doc://kypp/memory-scope-decay@0004#scope-keys-decay — filing puts an unsorted claim into a known project; origin is kept, the move is logged, and an unsure claim stays unsorted.
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
