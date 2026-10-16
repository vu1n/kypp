// Pinned static tokens (KYPP_API_TOKENS "name@projA|projB:token"): such a token reads and writes only
// its pinned projects, by header or argument; anything outside is refused and no project list leaks.
import assert from "node:assert/strict";
import { test } from "node:test";
import { defrag } from "../src/defrag.ts";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { DEFAULT_POLICY } from "../src/memory.ts";
import { handleRooms } from "../src/rooms.ts";
import { type S1Client } from "../src/s1.ts";
import { D1Store } from "../src/store.ts";
import { apiTokens, tokenPins } from "../src/tokens.ts";
import { memoryD1 } from "./d1.ts";

const TS = "2026-10-16T00:00:00Z";
const SECRET = "x".repeat(20);
const PINNED: Caller = { user: "ci", agent: "ci", projects: ["kypp"] };
const TWO: Caller = { user: "ci", agent: "ci", projects: ["kypp", "brief"] };

async function setup() {
  const db = memoryD1();
  for (const p of ["kypp", "brief", "secret-repo"]) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(p, "", TS).run();
  }
  return db;
}

function call(db: D1Database, caller: Caller, opts: { header?: string; session?: string; s1?: S1Client | null } = {}) {
  const headers: Record<string, string> = { "Mcp-Session-Id": opts.session ?? "s1" };
  if (opts.header) headers["X-Kypp-Project"] = opts.header;
  return async (name: string, args: Record<string, unknown> = {}) => {
    const req = new Request("https://k/mcp", { method: "POST", headers,
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, caller, { s1: opts.s1 ?? null })).json() as any;
    return { text: result.content[0].text as string, error: !!result.isError };
  };
}

const row = async (db: D1Database, id: string) =>
  (await db.prepare("SELECT * FROM memory_claims WHERE id = ?").bind(id).first()) as Record<string, any>;
const count = async (db: D1Database) => ((await db.prepare("SELECT COUNT(*) AS n FROM memory_claims").first()) as any).n;
const filer = (project: string): S1Client => ({
  systemOne: async ({ questions }) => ({ answers: "file" in questions ? { file: { type: "choice", choice: project, confidence: 0.99 } } : {} }),
});

test("the token format keeps plain pairs and adds an optional pin", () => {
  const m = apiTokens(`huddles:${SECRET}, ci@kypp|brief:${SECRET}y ,none@:${SECRET}z,short:abc`);
  assert.deepEqual(m.get(SECRET), { name: "huddles" });
  assert.deepEqual(m.get(`${SECRET}y`), { name: "ci", projects: ["kypp", "brief"] });
  assert.deepEqual(m.get(`${SECRET}z`), { name: "none", projects: [] }, "an empty pin allows nothing");
  assert.equal(m.size, 3);
  assert.deepEqual([...tokenPins(`huddles:${SECRET},ci@kypp|brief:${SECRET}y,ci@kypp:${SECRET}w`)], [["ci", ["kypp"]]]);
});

test("a pinned token can't read or claim another project by argument or header", async () => {
  const db = await setup();
  await call(db, { user: "owner", agent: "claude" }, { header: "secret-repo" })("claim", { subject: "s", content: "secret lesson" });
  for (const [header, args] of [[undefined, { project: "secret-repo" }], ["secret-repo", {}], ["kypp", { project: "secret-repo" }]] as const) {
    const c = call(db, PINNED, { header });
    for (const tool of ["briefing", "recall"]) {
      const out = await c(tool, { ...args, query: "secret" });
      assert.ok(out.error, `${tool} via ${header ?? "argument"} is refused`);
      assert.doesNotMatch(out.text, /secret lesson/);
    }
    const before = await count(db);
    assert.ok((await c("claim", { ...args, subject: "x", content: "a lesson" })).error);
    assert.equal(await count(db), before, "nothing is written");
  }
});

test("a pinned token uses its project from the pin, the header or the argument", async () => {
  const db = await setup();
  const id = (await call(db, PINNED)("claim", { subject: "s", content: "a lesson" })).text;
  assert.deepEqual([(await row(db, id)).project, (await row(db, id)).origin], ["kypp", "kypp"], "a single pin is the default");
  const viaHeader = (await call(db, TWO, { header: "brief" })("claim", { subject: "s", content: "a lesson" })).text;
  assert.equal((await row(db, viaHeader)).project, "brief");
  const viaArg = (await call(db, TWO, { header: "kypp" })("claim", { project: "vu1n/brief", subject: "s", content: "a lesson" })).text;
  assert.equal((await row(db, viaArg)).project, "brief", "the argument selects among pinned projects");
  const unnamed = await call(db, TWO)("claim", { subject: "s", content: "a lesson" });
  assert.ok(unnamed.error, "with several pinned projects a claim must name one");
  assert.match(unnamed.text, /brief, kypp/);
  const user = (await call(db, TWO)("claim", { subject: "u", content: "prefers short PRs", scope: "user" })).text;
  assert.equal((await row(db, user)).scope, "user");
});

test("an unknown name doesn't leak the project list to a pinned token", async () => {
  const db = await setup();
  for (const out of [await call(db, PINNED)("recall", { project: "nope", query: "x" }), await call(db, PINNED, { header: "nope" })("briefing"),
    await call(db, TWO)("briefing"), await call(db, PINNED)("claim", { project: "nope", subject: "s", content: "c" })]) {
    assert.doesNotMatch(out.text, /secret-repo|Known projects/);
  }
});

test("a pinned token can't expand another project's claim", async () => {
  const db = await setup();
  const secret = (await call(db, { user: "owner", agent: "claude" }, { header: "secret-repo" })("claim", { subject: "s", content: "secret lesson" })).text;
  const own = (await call(db, PINNED)("claim", { subject: "s", content: "a lesson" })).text;
  assert.ok((await call(db, PINNED)("expand", { handle: secret.slice(0, 12) })).error);
  assert.match((await call(db, PINNED)("expand", { handle: own.slice(0, 12) })).text, /a lesson/);
});

test("unpinned and OAuth callers behave as before", async () => {
  const db = await setup();
  for (const caller of [{ user: "huddles", agent: "huddles" }, { user: "owner", agent: "claude" }]) {
    const arg = (await call(db, caller, { header: "kypp" })("claim", { project: "secret-repo", subject: "s", content: "a lesson" })).text;
    assert.equal((await row(db, arg)).project, "secret-repo", "the argument wins");
    const fallback = (await call(db, caller, { header: "kypp" })("claim", { project: "nope", subject: "s", content: "a lesson" })).text;
    assert.equal((await row(db, fallback)).project, "kypp", "a wrong argument falls back to the header");
    const unsorted = await call(db, caller)("claim", { project: "nope", subject: "s", content: "a lesson" });
    assert.match(unsorted.text, /unsorted: "nope" is not a known project.*Known projects: brief, kypp, secret-repo/s);
  }
});

test("defrag never files a pinned token's claim outside its pin", async () => {
  const db = await setup();
  const store = new D1Store(db);
  // Written before the token was pinned, so it landed unsorted.
  const id = (await call(db, { user: "ci", agent: "ci" })("claim", { project: "secret-repo-x", subject: "s", content: "a lesson" })).text.split("\n")[0];
  const owner = (await call(db, { user: "owner", agent: "claude" })("claim", { subject: "s", content: "another lesson" })).text.split("\n")[0];
  const pins = new Map([["ci", ["kypp"]]]);
  assert.equal((await defrag(store, DEFAULT_POLICY, filer("secret-repo"), pins)).filed, 1, "only the unpinned caller's claim moves");
  assert.equal((await row(db, id)).project, null);
  assert.equal((await row(db, owner)).project, "secret-repo");
  await defrag(store, DEFAULT_POLICY, filer("kypp"), pins);
  assert.equal((await row(db, id)).project, "kypp");
});

test("defrag files a pinned token's claim by origin name only inside the pin", async () => {
  const db = await setup();
  const id = (await call(db, { user: "ci", agent: "ci" })("claim", { project: "secret-repo-x", subject: "s", content: "a lesson" })).text.split("\n")[0];
  await db.prepare("UPDATE memory_claims SET origin = 'secret-repo' WHERE id = ?").bind(id).run();
  await defrag(new D1Store(db), DEFAULT_POLICY, null, new Map([["ci", ["kypp"]]]));
  assert.equal((await row(db, id)).project, null);
});

test("a pinned room issuer links rooms only inside its pin", async () => {
  const db = await setup();
  const post = (links: string[]) => handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id: "r", links }) }),
    db, "huddles", { canLink: true, credential: "d", linkable: ["kypp"] });
  const refused = await post(["kypp", "secret-repo"]);
  assert.equal(refused.status, 403);
  assert.doesNotMatch(JSON.stringify(await refused.json()), /brief/);
  assert.equal((await post(["kypp"])).status, 201);
});
