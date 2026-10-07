import assert from "node:assert/strict";
import { test } from "node:test";
import { defrag } from "../src/defrag.ts";
import { type McpOptions, handleMcp } from "../src/mcp.ts";
import { compactLine, planConsolidation, projectList, projectName } from "../src/memory.ts";
import type { S1Client } from "../src/s1.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const caller = { user: "vu", agent: "t" };

// One MCP client: initialize once for a signed session, then call tools on it.
async function client(db: D1Database, opts: McpOptions, header: string | null = null) {
  let session: string | null = null;
  const send = async (method: string, params?: unknown) => {
    const headers: Record<string, string> = {};
    if (header) headers["X-Kypp-Project"] = header;
    if (session) headers["Mcp-Session-Id"] = session;
    const res = await handleMcp(new Request("https://k/mcp", { method: "POST", headers, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) }),
      db, caller, { sessionKey: "k", ...opts });
    session ??= res.headers.get("Mcp-Session-Id");
    return (await res.json()).result;
  };
  await send("initialize", {});
  return async (name: string, args: Record<string, unknown>) => (await send("tools/call", { name, arguments: args })).content[0].text as string;
}

// A gate that keeps everything and files into `into` with confidence `p` when asked.
const filer = (into: string, p = 0.9): S1Client => ({
  systemOne: async ({ questions }: any) => ({
    answers: { keep: { type: "noul", noul: 0.9 }, ...(questions.project ? { project: { type: "choice", choice: into, confidence: p } } : {}) },
  }),
});

test("project names normalize, and KYPP_PROJECTS parses to a list or null", () => {
  assert.equal(projectName("  Kypp "), "kypp");
  assert.equal(projectName("../etc"), null);
  assert.equal(projectName(""), null);
  assert.deepEqual(projectList("kypp, Brief,,kypp"), ["kypp", "brief"]);
  assert.equal(projectList(undefined), null);
});

test("an unknown project lands unsorted: the writer sees it, other sessions don't", async () => {
  const db = memoryD1();
  const opts = { projects: ["kypp", "brief"] };
  const a = await client(db, opts), b = await client(db, opts);
  const out = await a("claim", { subject: "fts quoting", content: "quote every word before MATCH", project: "dev" });
  assert.match(out, /unsorted \("dev" is not a known project\)/);
  const mine = await a("recall", { query: "MATCH quoting" });
  assert.match(mine, /\(unsorted\) fts quoting/);
  assert.equal(await b("recall", { query: "MATCH quoting", include_candidates: true }), "(no matching memory)");
  const row = (await new D1Store(db).unsorted())[0];
  assert.equal(row.origin, "dev");
  assert.equal(row.project, null);
});

test("with no project at all the write still lands", async () => {
  const db = memoryD1();
  const a = await client(db, {});
  const out = await a("claim", { subject: "s", content: "a durable lesson" });
  assert.match(out, /no project was given/);
  assert.equal((await new D1Store(db).unsorted()).length, 1);
});

test("without KYPP_PROJECTS a named project behaves as before", async () => {
  const db = memoryD1();
  const a = await client(db, {});
  const id = await a("claim", { subject: "s", content: "c", project: "Kypp" });
  assert.match(id, /^[0-9a-f]{32}$/);
  const c = await new D1Store(db).get(id, "vu");
  assert.deepEqual([c?.scope, c?.project, (c as any)?.origin], ["project", "kypp", "kypp"]);
});

test("the gate files an unsorted write in the same call when it is sure", async () => {
  const db = memoryD1();
  const a = await client(db, { projects: ["kypp", "brief"], s1: filer("brief") });
  const id = await a("claim", { subject: "s", content: "c" });
  const c = await new D1Store(db).get(id, "vu");
  assert.deepEqual([c?.scope, c?.project], ["project", "brief"]);
  const unsure = await client(db, { projects: ["kypp", "brief"], s1: filer("brief", 0.5) });
  assert.match(await unsure("claim", { subject: "t", content: "c" }), /unsorted/);
});

test("scope=user is still the agent's call; global is not", async () => {
  const db = memoryD1();
  const a = await client(db, { projects: ["kypp"] });
  const id = await a("claim", { subject: "tone", content: "short replies", scope: "user", project: "kypp" });
  assert.equal((await new D1Store(db).get(id, "vu"))?.scope, "user");
  const g = await a("claim", { subject: "x", content: "y", scope: "global", project: "kypp" });
  assert.equal((await new D1Store(db).get(g, "vu"))?.scope, "project");
});

test("defrag files by a now-known origin without the model, by the model when sure, and logs the move", async () => {
  const db = memoryD1();
  const store = new D1Store(db);
  const a = await client(db, { projects: ["kypp"] });
  const fromDev = (await a("claim", { subject: "one", content: "c", project: "dev" })).split("\n")[0];
  const noName = (await a("claim", { subject: "two", content: "c" })).split("\n")[0];
  const policy = { k: 2, minGapMs: 0, autoAccept: () => true };
  // Nothing known can take them and there's no model: they stay put.
  assert.equal((await defrag(store, policy, null, ["kypp"])).filed, 0);
  // "dev" becomes a known project; the model files the other one into kypp.
  const r = await defrag(store, policy, filer("kypp"), ["kypp", "dev"]);
  assert.equal(r.filed, 2);
  const one = await store.get(fromDev, "vu"), two = await store.get(noName, "vu");
  assert.deepEqual([one?.project, two?.project], ["dev", "kypp"]);
  const meta = (id: string) => db.prepare("SELECT metadata FROM memory_claims WHERE id = ?").bind(id).first();
  assert.equal(JSON.parse((await meta(fromDev))!.metadata as string).filed.by, "origin");
  assert.equal(JSON.parse((await meta(noName))!.metadata as string).filed.by, "s1");
  assert.equal(await store.file(fromDev, "kypp", { by: "s1" }), false, "a filed claim doesn't move again");
});

test("unsorted claims never promote, even when they recur", async () => {
  const store = new D1Store(memoryD1());
  for (const s of ["A", "B"]) {
    await store.claim({ type: "fact", subject: "s", content: "c", scope: "unsorted", project: null, origin: null, confidence: 0.7,
      sourceIds: [`session:${s}`], codeRefs: [], accept: false, agent: "a", user: "vu" });
  }
  assert.deepEqual(planConsolidation(await store.liveClaims()), []);
});

test("lines say which level they came from", () => {
  const base = { id: "a".repeat(32), type: "fact", subject: "s", content: "c", agent: null, user: null, status: "accepted",
    authority: "agent", confidence: 0.7, source_ids: [], code_refs: [], created_at: "", updated_at: "" } as any;
  assert.match(compactLine({ ...base, scope: "project", project: "kypp" }), /\] \(kypp\) s —/);
  assert.match(compactLine({ ...base, scope: "unsorted", project: null }), /\(unsorted\)/);
  assert.match(compactLine({ ...base, scope: "user", project: null }), /\(user\)/);
});
