// mcp.ts — kypp's agent tools as a stateless streamable-HTTP MCP endpoint (JSON responses, no SSE,
// no Durable Object). The same contract as kypp/mcp_server.py's briefing / recall / claim / expand /
// correct; consolidation is the scheduled pass in index.ts, not an agent tool.
import { type Claim, DEFAULT_POLICY, HUMAN_CORRECTION_CONFIDENCE, type Policy, TYPES, briefingOrder, ftsQuery, renderBriefing, renderClaims, resolveOrigin } from "./memory.ts";
import { consolidate } from "./consolidate.ts";
import { fileClaim } from "./defrag.ts";
import { DROP_BELOW, type ProjectInfo, type S1Client, judge } from "./s1.ts";
import { D1Store } from "./store.ts";

export interface Caller {
  user: string | null;    // who signed in (OAuth props) or the API token's name
  agent: string | null;   // the client that holds the token
}

const VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"];
const SESSION_HEADER = "Mcp-Session-Id";

const INSTRUCTIONS = `kypp is shared memory for coding agents — durable lessons governed by status
(candidate → accepted), authority (human > verified > agent) and provenance.
1. SESSION START — call \`briefing\` once. Pass \`project\` (the repo you are working in) on every call.
2. BEFORE non-trivial work — \`recall("<what you're about to touch>")\`; \`expand(handle)\` for the full claim.
3. WHEN YOU LEARN SOMETHING DURABLE — \`claim\` a distilled, model-agnostic lesson. \`subject\` is its
   identity: reuse a subject to update it. Claims land as candidates; one is accepted once a second
   session claims the same subject. Don't store status ("shipped", "PR merged") — git holds that.
   WHERE IT GOES: you don't choose. Say which repo you are in and the server files and shares the
   lesson. Set \`scope: "user"\` only for how this person works, in every repo.
4. A HUMAN GAVE YOU THE RIGHT ANSWER — \`correct(subject, content)\`.`;

const projectProp = { type: "string", description: "The repo you are working in. Optional when the client sends an X-Kypp-Project header. An unknown name is not an error: reads then cover user and global memory only, and a claim lands unsorted until the server files it." };
const TOOLS = [
  {
    name: "briefing",
    description: "Session-start digest — call ONCE before working: this project's strongest accepted memory plus global memory, pitfalls first. Lines carry handles; `expand` any you act on.",
    inputSchema: { type: "object", properties: { project: projectProp, limit: { type: "integer", default: 12 } } },
  },
  {
    name: "recall",
    description: "Search shared memory by keywords across the project, user and global scopes. One compact line per hit: `handle [type ✓conf @level] subject — content`. ✓ accepted, ? candidate; level is the project, `user`, `global`, or `unsorted` (your own claim, not yet filed). Accepted only unless include_candidates. `agent` limits hits to one client's claims.",
    inputSchema: {
      type: "object", required: ["query"],
      properties: {
        query: { type: "string" }, project: projectProp,
        types: { type: "array", items: { type: "string", enum: TYPES } },
        agent: { type: "string", description: "Only claims written by this client." },
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
        type: { type: "string", enum: TYPES, description: "Omit to let the server label it (defaults to fact)." },
        confidence: { type: "number", default: 0.7 },
        scope: { type: "string", enum: ["project", "user"], default: "project", description: "Leave as project for anything about a repo; the server decides how widely it is shared. user = how this person works, in every repo." },
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

interface Ctx { project: string | null; session: string | null; caller: Caller; s1: S1Client | null; policy: Policy; correct: boolean }

export interface McpOptions {
  s1?: S1Client | null;
  policy?: Policy;
  sessionKey?: string; // HMAC key for session ids; without one, ids are unsigned
  correct?: boolean;   // offer the trust-based `correct` tool (default on; KYPP_CORRECT=off hides it)
}

// Session ids are `<id>.<mac>`, signed at initialize, so a client can't name an arbitrary session to
// fake recurrence. A missing or bad signature means no session: claims get no stamp.
async function mac(key: string, id: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(id)));
  return [...sig.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function issueSession(key: string | undefined): Promise<string> {
  const id = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  return key ? `${id}.${await mac(key, id)}` : id;
}

export async function verifySession(key: string | undefined, header: string | null): Promise<string | null> {
  if (!header) return null;
  if (!key) return header;
  const [id, sig] = header.split(".");
  return id && sig && sig === (await mac(key, id)) ? id : null;
}

const known = (projects: ProjectInfo[]) => projects.map((p) => p.name).join(", ") || "none registered";

async function callTool(store: D1Store, name: string, a: Args, ctx: Ctx): Promise<string> {
  // Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — the caller says where it is; only a registered project is a place, so an unknown name can't start a separate memory.
  const origin: string | null = a.project || ctx.project || null;
  const projects = name === "expand" ? [] : await store.projects();
  const project = resolveOrigin(origin, projects.map((p) => p.name));
  const user = ctx.caller.user;
  // Why: a read without a known project used to return less with no sign of it.
  const note = project ? "" : `note: ${origin ? `"${origin}" is not a known project` : "no project given"}, so this shows user and global memory only. Known projects: ${known(projects)}.\n`;
  switch (name) {
    case "briefing": {
      const limit = Math.min(a.limit ?? 12, 30);
      const claims = briefingOrder(await store.recall("", project, user, { limit: limit * 3 }), limit);
      await store.recordUsage(ctx.session, claims, "briefing", project);
      return note + renderBriefing(claims);
    }
    case "recall": {
      const claims = await store.recall(ftsQuery(String(a.query ?? "")), project, user,
        { includeCandidates: !!a.include_candidates, types: a.types, agent: a.agent, limit: a.limit, session: ctx.session });
      await store.recordUsage(ctx.session, claims, "recall", project, a.query || null);
      return note + renderClaims(claims);
    }
    case "claim": {
      const subject = String(a.subject ?? ""), content = String(a.content ?? "");
      // Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — a cheap model gates writes; it filters, labels and files, never accepts or widens a claim.
      const v = await judge(ctx.s1, { subject, content, project });
      if (v.keep !== null && v.keep < DROP_BELOW) {
        return `Not stored: this reads as status or session detail rather than a durable lesson (p_keep=${v.keep.toFixed(2)}). Git and PRs already hold status.`;
      }
      const s1 = v.keep === null ? undefined : { keep: v.keep, general: v.general, type: v.type };
      // Why: the agent picks who a lesson is about (user), never how widely it applies; a global request is filed like any other.
      const scope = a.scope === "user" ? "user" : "project";
      // Why: the session stamp is what lets the scheduled pass's two-session gate count agreement.
      const id = await store.claim({
        type: a.type ?? v.type ?? "fact", subject, content,
        scope, project, origin, session: ctx.session, confidence: a.confidence ?? 0.7,
        sourceIds: ctx.session ? [`session:${ctx.session}`] : [], codeRefs: a.code_refs,
        accept: false, agent: ctx.caller.agent, user, metadata: s1 ? { s1 } : {},
      });
      // Why: clients that can't send a header shouldn't wait an hour for defrag to file their claim.
      if (scope === "user" || project || await fileClaim(store, ctx.s1, { id, subject, content, origin }, projects)) return id;
      return `${id}\nunsorted: ${origin ? `"${origin}" is not a known project` : "no project given"}, so only this session can recall it until the server files it. Known projects: ${known(projects)}.`;
    }
    case "expand": {
      const c = await store.get(String(a.handle ?? ""), user);
      if (!c) throw new Error(`unknown claim handle ${a.handle}`);
      await store.recordUsage(ctx.session, [c], "expand", c.project);
      return JSON.stringify(claimDict(c), null, 2);
    }
    case "correct": {
      if (!ctx.correct) throw new Error("correct is turned off on this server; use claim instead");
      if (!project) throw new Error(`correct needs a known project (${known(projects)})`);
      const subject = String(a.subject ?? "");
      const id = await store.claim({
        type: a.type ?? "fact", subject, content: String(a.content ?? ""), scope: "project", project,
        confidence: HUMAN_CORRECTION_CONFIDENCE, sourceIds: ctx.session ? [`session:${ctx.session}`] : [],
        codeRefs: [], accept: true, agent: ctx.caller.agent, user,
      });
      await consolidate(store, ctx.policy, ctx.s1, { scope: "project", project, subject });
      return id;
    }
  }
  throw new Error(`unknown tool ${name}`);
}

async function dispatch(msg: Rpc, store: D1Store, ctx: Ctx) {
  const ok = (result: unknown) => ({ jsonrpc: "2.0", id: msg.id, result });
  const err = (code: number, message: string) => ({ jsonrpc: "2.0", id: msg.id ?? null, error: { code, message } });
  switch (msg.method) {
    case "initialize": {
      const asked = msg.params?.protocolVersion;
      return ok({
        protocolVersion: VERSIONS.includes(asked) ? asked : VERSIONS[2],
        capabilities: { tools: {} },
        serverInfo: { name: "kypp", version: "0.1.0" },
        instructions: ctx.correct ? INSTRUCTIONS : INSTRUCTIONS.slice(0, INSTRUCTIONS.indexOf("\n4.")),
      });
    }
    case "ping":
      return ok({});
    case "tools/list":
      return ok({ tools: ctx.correct ? TOOLS : TOOLS.filter((t) => t.name !== "correct") });
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

// Stateless: the session id handed out at initialize is only a signed identity the client echoes
// back, so usage and claims from one session group together. Nothing is held between requests.
export async function handleMcp(request: Request, db: D1Database, caller: Caller, opts: McpOptions = {}): Promise<Response> {
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
  const issued = isInit ? await issueSession(opts.sessionKey) : null;
  const session = issued ? issued.split(".")[0] : await verifySession(opts.sessionKey, request.headers.get(SESSION_HEADER));
  const store = new D1Store(db);
  const ctx: Ctx = { project, session, caller, s1: opts.s1 ?? null, policy: opts.policy ?? DEFAULT_POLICY, correct: opts.correct ?? true };
  const replies = [];
  for (const m of msgs) {
    if (m.id === undefined) continue; // notifications get no reply
    replies.push(await dispatch(m, store, ctx));
  }
  const headers = new Headers(issued ? { [SESSION_HEADER]: issued } : {});
  if (!replies.length) return new Response(null, { status: 202, headers });
  return Response.json(Array.isArray(body) ? replies : replies[0], { headers });
}
