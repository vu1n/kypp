// mcp.ts — kypp's agent tools as a stateless streamable-HTTP MCP endpoint (JSON responses, no SSE,
// no Durable Object). The same contract as kypp/mcp_server.py's briefing / recall / claim / expand /
// correct; consolidation is the scheduled pass in index.ts, not an agent tool.
import { type Claim, HUMAN_CORRECTION_CONFIDENCE, TYPES, briefingOrder, ftsQuery, planConsolidation, renderClaims } from "./memory.ts";
import { D1Store } from "./store.ts";

export interface Caller {
  user: string | null;    // who signed in (OAuth props) or the API token's name
  agent: string | null;   // the client that holds the token
}

const VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"];
const SESSION_HEADER = "Mcp-Session-Id";

const INSTRUCTIONS = `kypp is shared memory for coding agents — durable lessons governed by status
(candidate → accepted), authority (human > verified > agent) and provenance.
1. SESSION START — call \`briefing\` once. Pass \`project\` (the repo name) on every call.
2. BEFORE non-trivial work — \`recall("<what you're about to touch>")\`; \`expand(handle)\` for the full claim.
3. WHEN YOU LEARN SOMETHING DURABLE — \`claim\` a distilled, model-agnostic lesson. \`subject\` is its
   identity: reuse a subject to update it. Claims land as candidates; one is accepted once a second
   session claims the same subject. Don't store status ("shipped", "PR merged") — git holds that.
4. A HUMAN GAVE YOU THE RIGHT ANSWER — \`correct(subject, content)\`.`;

const projectProp = { type: "string", description: "Repo name. Optional when the client sends an X-Kypp-Project header." };
const TOOLS = [
  {
    name: "briefing",
    description: "Session-start digest — call ONCE before working: this project's strongest accepted memory plus global memory, pitfalls first. Lines carry handles; `expand` any you act on.",
    inputSchema: { type: "object", properties: { project: projectProp, limit: { type: "integer", default: 12 } } },
  },
  {
    name: "recall",
    description: "Search shared memory by keywords. One compact line per hit: `handle [type ✓conf] subject — content`. ✓ accepted, ? candidate. Accepted only unless include_candidates.",
    inputSchema: {
      type: "object", required: ["query"],
      properties: {
        query: { type: "string" }, project: projectProp,
        types: { type: "array", items: { type: "string", enum: TYPES } },
        include_candidates: { type: "boolean", default: false }, limit: { type: "integer", default: 10 },
      },
    },
  },
  {
    name: "claim",
    description: "Record a durable, distilled lesson (not a transcript, not project status). `subject` is the claim's identity: reuse an existing subject to update it. Lands as a candidate; accepted once another session claims the same subject. Keep content model-agnostic. Returns the claim id.",
    inputSchema: {
      type: "object", required: ["subject", "content"],
      properties: {
        subject: { type: "string" }, content: { type: "string" },
        type: { type: "string", enum: TYPES, default: "fact" },
        confidence: { type: "number", default: 0.7 },
        scope: { type: "string", enum: ["project", "global"], default: "project" },
        project: projectProp,
        code_refs: { type: "array", items: { type: "object" }, description: "[{symbol, path, query}] anchors" },
      },
    },
  },
  {
    name: "expand",
    description: "Dereference a handle (claim id or 8-char prefix) to the full claim with provenance. May resolve superseded history — check `status`.",
    inputSchema: { type: "object", required: ["handle"], properties: { handle: { type: "string" } } },
  },
  {
    name: "correct",
    description: "Record the right answer a human gave you for a subject memory got wrong. Lands accepted at top confidence and supersedes weaker agent claims on the subject, at agent authority (only the operator's local `kypp correct` writes human authority). Returns the claim id.",
    inputSchema: {
      type: "object", required: ["subject", "content"],
      properties: { subject: { type: "string" }, content: { type: "string" }, type: { type: "string", enum: TYPES, default: "fact" }, project: projectProp },
    },
  },
];

type Args = Record<string, any>;
type Rpc = { jsonrpc: "2.0"; id?: string | number | null; method: string; params?: any };

function claimDict(c: Claim) {
  const { id, type, subject, content, scope, project, status, authority, confidence, source_ids, code_refs, agent, user, created_at } = c;
  return { id, type, subject, content, scope, project, status, authority, confidence, source_ids, code_refs, agent, user, created_at };
}

async function callTool(store: D1Store, name: string, a: Args, ctx: { project: string | null; session: string | null; caller: Caller }): Promise<string> {
  const project: string | null = a.project || ctx.project;
  switch (name) {
    case "briefing": {
      const limit = Math.min(a.limit ?? 12, 30);
      const claims = briefingOrder(await store.recall("", project, { limit: limit * 3 }), limit);
      await store.recordUsage(ctx.session, claims, "briefing", project);
      return renderClaims(claims, "(no accepted memory yet)");
    }
    case "recall": {
      const claims = await store.recall(ftsQuery(String(a.query ?? "")), project,
        { includeCandidates: !!a.include_candidates, types: a.types, limit: a.limit });
      await store.recordUsage(ctx.session, claims, "recall", project, a.query || null);
      return renderClaims(claims);
    }
    case "claim":
      // Why: the session stamp is what lets the scheduled pass's two-session gate count agreement.
      return store.claim({
        type: a.type ?? "fact", subject: String(a.subject ?? ""), content: String(a.content ?? ""),
        scope: a.scope ?? "project", project, confidence: a.confidence ?? 0.7,
        sourceIds: ctx.session ? [`session:${ctx.session}`] : [], codeRefs: a.code_refs ?? [],
        accept: false, agent: ctx.caller.agent, user: ctx.caller.user,
      });
    case "expand": {
      const c = await store.get(String(a.handle ?? ""));
      if (!c) throw new Error(`unknown claim handle ${a.handle}`);
      await store.recordUsage(ctx.session, [c], "expand", c.project);
      return JSON.stringify(claimDict(c), null, 2);
    }
    case "correct": {
      const subject = String(a.subject ?? "");
      const id = await store.claim({
        type: a.type ?? "fact", subject, content: String(a.content ?? ""), scope: "project", project,
        confidence: HUMAN_CORRECTION_CONFIDENCE, sourceIds: ctx.session ? [`session:${ctx.session}`] : [],
        codeRefs: [], accept: true, agent: ctx.caller.agent, user: ctx.caller.user,
      });
      await store.apply(planConsolidation(await store.liveClaims(project, subject)));
      return id;
    }
  }
  throw new Error(`unknown tool ${name}`);
}

async function dispatch(msg: Rpc, store: D1Store, ctx: { project: string | null; session: string | null; caller: Caller }) {
  const ok = (result: unknown) => ({ jsonrpc: "2.0", id: msg.id, result });
  const err = (code: number, message: string) => ({ jsonrpc: "2.0", id: msg.id ?? null, error: { code, message } });
  switch (msg.method) {
    case "initialize": {
      const asked = msg.params?.protocolVersion;
      return ok({
        protocolVersion: VERSIONS.includes(asked) ? asked : VERSIONS[2],
        capabilities: { tools: {} },
        serverInfo: { name: "kypp", version: "0.1.0" },
        instructions: INSTRUCTIONS,
      });
    }
    case "ping":
      return ok({});
    case "tools/list":
      return ok({ tools: TOOLS });
    case "tools/call":
      try {
        const text = await callTool(store, msg.params?.name, msg.params?.arguments ?? {}, ctx);
        return ok({ content: [{ type: "text", text }] });
      } catch (e) {
        return ok({ content: [{ type: "text", text: String((e as Error).message ?? e) }], isError: true });
      }
    default:
      return err(-32601, `method not found: ${msg.method}`);
  }
}

// Stateless: the session id handed out at initialize is only an identity the client echoes back,
// so usage and claims from one session group together. Nothing is held between requests.
export async function handleMcp(request: Request, db: D1Database, caller: Caller): Promise<Response> {
  if (request.method !== "POST") return new Response("POST JSON-RPC to this endpoint", { status: 405, headers: { Allow: "POST" } });
  const project = request.headers.get("X-Kypp-Project");
  let body: Rpc | Rpc[];
  try {
    body = await request.json();
  } catch {
    return Response.json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } }, { status: 400 });
  }
  const msgs = Array.isArray(body) ? body : [body];
  const isInit = msgs.some((m) => m.method === "initialize");
  const session = isInit ? crypto.randomUUID().replaceAll("-", "").slice(0, 12) : request.headers.get(SESSION_HEADER);
  const store = new D1Store(db);
  const ctx = { project, session, caller };
  const replies = [];
  for (const m of msgs) {
    if (m.id === undefined) continue; // notifications get no reply
    replies.push(await dispatch(m, store, ctx));
  }
  const headers = new Headers(isInit && session ? { [SESSION_HEADER]: session } : {});
  if (!replies.length) return new Response(null, { status: 202, headers });
  return Response.json(Array.isArray(body) ? replies : replies[0], { headers });
}
