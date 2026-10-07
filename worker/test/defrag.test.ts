// Placement (memory-scope-decay §8, step 0): a write records its origin and never fails for lack of
// a project; unknown or missing origins land unsorted; defrag files them; reads label the level.
import assert from "node:assert/strict";
import { test } from "node:test";
import { defrag } from "../src/defrag.ts";
import { handleMcp } from "../src/mcp.ts";
import { DEFAULT_POLICY, resolveOrigin } from "../src/memory.ts";
import { FILE_ABOVE, NO_PROJECT, type S1Client, fileUnder } from "../src/s1.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const PROJECTS = [{ name: "kypp", description: "shared memory server" }, { name: "onoda", description: "agent harness" }];

async function setup() {
  const db = memoryD1();
  for (const p of PROJECTS) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(p.name, p.description, "2026-10-07T00:00:00Z").run();
  }
  return db;
}

// One MCP client: its own session, optional project header, optional S1.
function client(db: D1Database, opts: { header?: string; s1?: S1Client | null; session?: string } = {}) {
  const headers: Record<string, string> = { "Mcp-Session-Id": opts.session ?? "s1" };
  if (opts.header) headers["X-Kypp-Project"] = opts.header;
  return async (name: string, args: Record<string, unknown>): Promise<{ text: string; isError?: boolean }> => {
    const req = new Request("https://k/mcp", { method: "POST", headers,
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, { user: "owner", agent: "t" }, { s1: opts.s1 ?? null })).json() as any;
    return { text: result.content[0].text, isError: result.isError };
  };
}
const row = async (db: D1Database, id: string) =>
  (await db.prepare("SELECT * FROM memory_claims WHERE id = ?").bind(id).first()) as Record<string, any>;
const idOf = (text: string) => text.split("\n")[0];

// Files everything under `project` with the given confidence.
const filer = (project: string, confidence: number): S1Client => ({
  systemOne: async ({ questions }) => ({ answers: "file" in questions ? { file: { type: "choice", choice: project, confidence } } : {} }),
});

test("an origin resolves to a known project by name, ignoring case, owner prefix and .git", () => {
  const known = ["kypp", "onoda"];
  assert.equal(resolveOrigin("kypp", known), "kypp");
  assert.equal(resolveOrigin("vu1n/Kypp", known), "kypp");
  assert.equal(resolveOrigin("https://github.com/vu1n/onoda.git", known), "onoda");
  assert.equal(resolveOrigin("dev", known), null);
  assert.equal(resolveOrigin(null, known), null);
  assert.equal(resolveOrigin("  ", known), null);
  // names registered before this rule (any string was a project) stay reachable, exact match first
  assert.equal(resolveOrigin("vu1n/kypp", ["kypp", "vu1n/kypp"]), "vu1n/kypp");
  assert.equal(resolveOrigin("kypp", ["vu1n/kypp"]), "vu1n/kypp");
  assert.equal(resolveOrigin("kypp", ["Kypp", "kypp"]), "kypp");
});

test("an unknown project argument falls back to a known header", async () => {
  const db = await setup();
  const out = await client(db, { header: "kypp" })("claim", { project: "dev", subject: "s", content: "a durable lesson" });
  const r = await row(db, out.text);
  assert.deepEqual([r.project, r.origin], ["kypp", "dev"]);
  assert.ok(!(await client(db, { header: "kypp" })("recall", { project: "dev", query: "durable" })).text.startsWith("note:"));
});

test("a write from a known project is filed there and keeps its origin", async () => {
  const db = await setup();
  const out = await client(db, { header: "vu1n/kypp" })("claim", { subject: "s", content: "a durable lesson" });
  assert.match(out.text, /^[0-9a-f]{32}$/);
  const r = await row(db, out.text);
  assert.deepEqual([r.project, r.origin, r.session, r.scope], ["kypp", "vu1n/kypp", "s1", "project"]);
});

test("a write with an unknown or missing project lands unsorted; it never fails or creates a project", async () => {
  const db = await setup();
  for (const args of [{ project: "dev" }, {}]) {
    const out = await client(db)("claim", { subject: `s ${JSON.stringify(args)}`, content: "a durable lesson", ...args });
    assert.ok(!out.isError, out.text);
    assert.match(out.text, /\nunsorted: /);
    assert.match(out.text, /kypp, onoda/);
    const r = await row(db, idOf(out.text));
    assert.deepEqual([r.project, r.origin, r.status], [null, (args as any).project ?? null, "candidate"]);
  }
  const { results } = await db.prepare("SELECT name FROM projects ORDER BY name").all();
  assert.deepEqual(results.map((p) => p.name), ["kypp", "onoda"]);
});

test("an agent can't write global scope; user scope is still its choice", async () => {
  const db = await setup();
  const c = client(db, { header: "kypp" });
  for (const scope of ["global", "User"]) {
    const bad = await c("claim", { subject: "g", content: "true everywhere", scope });
    assert.ok(bad.isError && /bad scope/.test(bad.text), bad.text);
  }
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM memory_claims").first() as any).n, 0);
  const u = await row(db, idOf((await c("claim", { subject: "u", content: "prefers short PRs", scope: "user" })).text));
  assert.deepEqual([u.scope, u.project, u.origin, u.user], ["user", null, "kypp", "owner"]);
});

test("an unsorted claim is filed at write time when the model is sure, and the move is logged", async () => {
  const db = await setup();
  const out = await client(db, { s1: filer("onoda", 0.9) })("claim", { subject: "s", content: "the harness retries tool calls once" });
  assert.match(out.text, /^[0-9a-f]{32}$/, "filed claims return just the id");
  const r = await row(db, out.text);
  assert.deepEqual([r.project, r.origin], ["onoda", null]);
  const log = (await db.prepare("SELECT * FROM defrag_log WHERE claim_id = ?").bind(r.id).first()) as Record<string, any>;
  assert.deepEqual([log.action, log.from_project, log.to_project, log.score], ["file", null, "onoda", 0.9]);
});

test("defrag files what the write left unsorted; an unsure or unknown answer leaves it", async () => {
  const db = await setup();
  const id = idOf((await client(db)("claim", { subject: "s", content: "a lesson" })).text);
  const store = new D1Store(db);
  for (const s1 of [null, filer("onoda", FILE_ABOVE - 0.1), filer("not-a-project", 0.99), filer(NO_PROJECT, 0.99)]) {
    assert.equal((await defrag(store, DEFAULT_POLICY, s1)).filed, 0);
    assert.equal((await row(db, id)).project, null);
  }
  assert.equal((await defrag(store, DEFAULT_POLICY, filer("onoda", 0.9))).filed, 1);
  assert.equal((await row(db, id)).project, "onoda");
  assert.equal((await defrag(store, DEFAULT_POLICY, filer("kypp", 0.9))).filed, 0, "a filed claim is not refiled");
});

test("unsorted claims are not promoted until they are filed", async () => {
  const db = await setup();
  const a = idOf((await client(db, { session: "a" })("claim", { subject: "same", content: "x" })).text);
  const b = idOf((await client(db, { session: "b" })("claim", { subject: "same", content: "x again" })).text);
  const store = new D1Store(db);
  await defrag(store, DEFAULT_POLICY, null);
  assert.deepEqual([(await row(db, a)).status, (await row(db, b)).status], ["candidate", "candidate"]);
  await defrag(store, DEFAULT_POLICY, filer("kypp", 0.9));
  const statuses = [(await row(db, a)).status, (await row(db, b)).status].sort();
  assert.deepEqual(statuses, ["accepted", "superseded"], "filed together, two sessions now recur");
});

test("only the writing session sees its unsorted claims", async () => {
  const db = await setup();
  const id = idOf((await client(db, { session: "a" })("claim", { subject: "zebra", content: "zebra lesson" })).text);
  const q = { query: "zebra", include_candidates: true };
  assert.ok((await client(db, { session: "a" })("recall", q)).text.includes(id.slice(0, 8)));
  assert.ok((await client(db, { session: "a", header: "kypp" })("recall", q)).text.includes(id.slice(0, 8)));
  assert.ok(!(await client(db, { session: "b", header: "kypp" })("recall", q)).text.includes(id.slice(0, 8)));
});

test("reads label each line's level, and say so when the call has no known project", async () => {
  const db = await setup();
  const c = client(db, { header: "kypp", session: "a" });
  await c("claim", { subject: "zebra one", content: "zebra in the repo" });
  await c("claim", { subject: "zebra two", content: "zebra habits", scope: "user" });
  await client(db, { session: "a" })("claim", { subject: "zebra three", content: "zebra somewhere" });
  const here = (await c("recall", { query: "zebra", include_candidates: true })).text;
  assert.match(here, /\[fact \?0\.7 @kypp\] zebra one/);
  assert.match(here, /\[fact \?0\.7 @user\] zebra two/);
  assert.match(here, /\[fact \?0\.7 @unsorted\] zebra three/);
  assert.ok(!here.startsWith("note:"));
  for (const args of [{ project: "dev" }, {}]) {
    const lost = (await client(db, { session: "b" })("recall", { query: "zebra", include_candidates: true, ...args })).text;
    assert.match(lost, /^note: .*user and global memory only.*kypp, onoda/);
    assert.ok(!lost.includes("zebra one"));
    assert.match(lost, /zebra two/);
  }
  assert.match((await client(db, { session: "b" })("briefing", {})).text, /^note: /);
});

test("fileUnder returns a known project only when the model is confident", async () => {
  const claim = { subject: "s", content: "c", origin: "dev" };
  assert.equal(await fileUnder(null, claim, PROJECTS), null);
  assert.equal(await fileUnder(filer("kypp", 0.9), claim, []), null);
  assert.deepEqual(await fileUnder(filer("kypp", 0.9), claim, PROJECTS), { project: "kypp", confidence: 0.9 });
  assert.equal(await fileUnder(filer("kypp", FILE_ABOVE - 0.01), claim, PROJECTS), null);
  assert.equal(await fileUnder({ systemOne: async () => { throw new Error("down"); } }, claim, PROJECTS), null);
  let asked: any;
  await fileUnder({ systemOne: async (req) => { asked = req; return { answers: {} }; } }, claim, PROJECTS);
  assert.deepEqual(Object.keys(asked.questions.file.criteria), ["kypp", "onoda", NO_PROJECT]);
  const odd = [{ name: "__proto__", description: "" }, { name: NO_PROJECT, description: "a repo with the reserved name" }];
  await fileUnder({ systemOne: async (req) => { asked = req; return { answers: {} }; } }, claim, odd);
  assert.deepEqual(Object.keys(asked.questions.file.criteria), ["__proto__", NO_PROJECT]);
  assert.match(asked.questions.file.criteria[NO_PROJECT], /not clearly about/);
  assert.deepEqual(asked.state, claim);
});

test("filing keeps the claim's date, so an old stray can't pass for an update to a newer answer", async () => {
  const db = await setup();
  const id = idOf((await client(db)("claim", { subject: "s", content: "a lesson" })).text);
  const before = (await row(db, id)).updated_at;
  await new Promise((r) => setTimeout(r, 5));
  assert.equal((await defrag(new D1Store(db), DEFAULT_POLICY, filer("kypp", 0.9))).filed, 1);
  assert.equal((await row(db, id)).updated_at, before);
});

test("defrag rotates through unsorted claims instead of re-asking about the same ones", async () => {
  const db = await setup();
  const store = new D1Store(db);
  const ids: string[] = [];
  for (let i = 0; i < 30; i++) ids.push(idOf((await client(db)("claim", { subject: `s${i}`, content: `lesson ${i}` })).text));
  const asked: string[] = [];
  const unsure: S1Client = { systemOne: async ({ state }) => { asked.push((state as any).subject); return { answers: {} }; } };
  await defrag(store, DEFAULT_POLICY, unsure);
  await defrag(store, DEFAULT_POLICY, unsure);
  assert.equal(new Set(asked).size, 30, "the second pass reached the claims the first one didn't");
});

test("a write-time filing failure still returns the stored claim's id", async () => {
  const db = await setup();
  const boom = { ...db, prepare: (sql: string) => { if (/UPDATE memory_claims SET project/.test(sql)) throw new Error("d1 down"); return db.prepare(sql); } } as D1Database;
  const out = await client(boom, { s1: filer("kypp", 0.9) })("claim", { subject: "s", content: "a lesson" });
  assert.ok(!out.isError, out.text);
  assert.match(out.text, /^[0-9a-f]{32}\nunsorted: /);
});

test("without a session the unsorted reply doesn't promise a recall", async () => {
  const db = await setup();
  const req = new Request("https://k/mcp", { method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "claim", arguments: { subject: "s", content: "c" } } }) });
  const { result } = await (await handleMcp(req, db, { user: "owner", agent: "t" })).json() as any;
  assert.match(result.content[0].text, /it can't be recalled until the server files it/);
});
