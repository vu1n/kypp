// Room lanes (memory-scope-decay §11): a chat room's bot reads and writes only its own lane and the
// repos its room is linked to; nothing leaves the lane except an explicit share into a linked repo.
import assert from "node:assert/strict";
import { test } from "node:test";
import { defrag } from "../src/defrag.ts";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { DEFAULT_POLICY } from "../src/memory.ts";
import { handleRooms, roomCaller } from "../src/rooms.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const TS = "2026-10-14T00:00:00Z";

async function setup() {
  const db = memoryD1();
  for (const p of ["huddles", "pillbox"]) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(p, "", TS).run();
  }
  return db;
}

async function register(db: D1Database, id: string, links: string[] = [], issuer = "huddles") {
  const res = await handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id, links }) }), db, issuer, { canLink: true });
  return { status: res.status, body: await res.json() as any };
}

function call(db: D1Database, caller: Caller, session: string, headers: Record<string, string> = {}) {
  return async (name: string, args: Record<string, unknown> = {}) => {
    const req = new Request("https://k/mcp", { method: "POST", headers: { "Mcp-Session-Id": session, ...headers },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, caller)).json() as any;
    return { text: result.content[0].text as string, error: !!result.isError };
  };
}

async function roomClient(db: D1Database, id: string, links: string[] = [], session = "s1") {
  const { body } = await register(db, id, links);
  const caller = await roomCaller(db, body.token, ["huddles"]);
  assert.ok(caller);
  return call(db, caller, session);
}

const rows = async (db: D1Database) => (await db.prepare("SELECT * FROM memory_claims").all()).results as Record<string, any>[];

test("registering a room returns a token once and keeps only its digest", async () => {
  const db = await setup();
  const { status, body } = await register(db, "room-a", ["huddles"]);
  assert.equal(status, 201);
  assert.equal(body.room, "room:huddles:room-a");
  assert.match(body.token, /^kypr_/);
  const stored = (await db.prepare("SELECT * FROM rooms").all()).results[0] as Record<string, string>;
  assert.notEqual(stored.token_digest, body.token);
  assert.deepEqual(await roomCaller(db, body.token, ["huddles"]), { user: "room:huddles:room-a", agent: "room:huddles:room-a", room: "huddles:room-a" });
  assert.equal(await roomCaller(db, "kypr_nope", ["huddles"]), null);
});

test("links must be registered projects", async () => {
  const db = await setup();
  const { status, body } = await register(db, "room-a", ["huddles", "secret-repo"]);
  assert.equal(status, 400);
  assert.match(body.error, /secret-repo/);
});

test("re-registering rotates the token; a revoked room stays revoked", async () => {
  const db = await setup();
  const first = (await register(db, "room-a")).body.token;
  const again = await register(db, "room-a", ["pillbox"]);
  assert.equal(again.status, 200);
  assert.equal(await roomCaller(db, first, ["huddles"]), null);
  assert.ok(await roomCaller(db, again.body.token, ["huddles"]));
  await handleRooms(new Request("https://k/rooms/room-a", { method: "DELETE" }), db, "huddles", { canLink: true });
  assert.equal(await roomCaller(db, again.body.token, ["huddles"]), null);
  assert.equal((await register(db, "room-a")).status, 409);
});

test("a room's claims stay in its lane, whatever project or scope the model asks for", async () => {
  const db = await setup();
  const a = await roomClient(db, "room-a");
  const id = (await a("claim", { subject: "answer length", content: "This room wants answers under three sentences.", project: "huddles", scope: "user" })).text;
  const [row] = await rows(db);
  assert.equal(row.id, id);
  assert.equal(row.scope, "room");
  assert.equal(row.user, "room:huddles:room-a");
  assert.equal(row.project, null);
  assert.match((await a("recall", { query: "answers" })).text, /answer length/);
});

test("another room, the owner and a coding agent never see a room's lane", async () => {
  const db = await setup();
  const a = await roomClient(db, "room-a");
  const id = (await a("claim", { subject: "launch date", content: "The launch moved to November, privately." })).text;
  const b = await roomClient(db, "room-b", [], "s2");
  assert.doesNotMatch((await b("recall", { query: "launch" })).text, /launch date/);
  assert.equal((await b("expand", { handle: id.slice(0, 8) })).error, true);
  const owner = call(db, { user: "owner", agent: "t" }, "s3", { "X-Kypp-Project": "huddles" });
  assert.doesNotMatch((await owner("recall", { query: "launch", include_candidates: true })).text, /launch date/);
  assert.equal((await owner("expand", { handle: id.slice(0, 8) })).error, true);
});

test("a room reads a linked repo's settled memory, and no other repo's", async () => {
  const db = await setup();
  const coder = call(db, { user: "owner", agent: "t" }, "c1", { "X-Kypp-Project": "huddles" });
  const other = call(db, { user: "owner", agent: "t" }, "c2", { "X-Kypp-Project": "pillbox" });
  const linked = (await coder("claim", { subject: "deploy day", content: "Deploys happen on Tuesdays." })).text;
  const hidden = (await other("claim", { subject: "deploy window", content: "Pillbox deploys only at night." })).text;
  await db.prepare("UPDATE memory_claims SET status = 'accepted'").run();
  const a = await roomClient(db, "room-a", ["huddles"]);
  const seen = (await a("recall", { query: "deploys" })).text;
  assert.match(seen, /deploy day/);
  assert.doesNotMatch(seen, /deploy window/);
  assert.equal((await a("expand", { handle: linked.slice(0, 8) })).error, false);
  assert.equal((await a("expand", { handle: hidden.slice(0, 8) })).error, true);
});

test("reads never list registered projects to a room", async () => {
  const db = await setup();
  const a = await roomClient(db, "room-a");
  for (const tool of ["briefing", "recall"]) {
    assert.doesNotMatch((await a(tool, { query: "anything" })).text, /pillbox|Known projects/);
  }
});

test("sharing works only into a linked repo, and records where it came from", async () => {
  const db = await setup();
  const a = await roomClient(db, "room-a", ["huddles"]);
  assert.match((await a("claim", { subject: "x", content: "A lesson for pillbox.", share_to: "pillbox" })).text, /linked repos: huddles/);
  await a("claim", { subject: "release notes", content: "Release notes list user-visible changes first.", share_to: "huddles" });
  const [row] = await rows(db);
  assert.equal(row.scope, "project");
  assert.equal(row.project, "huddles");
  assert.equal(JSON.parse(row.metadata).shared_from, "room:huddles:room-a");
  const coder = call(db, { user: "owner", agent: "t" }, "c1", { "X-Kypp-Project": "huddles" });
  assert.match((await coder("recall", { query: "release" })).text, /release notes/);
});

test("defrag promotes a lesson inside a lane but never moves it out", async () => {
  const db = await setup();
  const a1 = await roomClient(db, "room-a");
  await a1("claim", { subject: "tone", content: "Keep replies informal here." });
  const caller = (await roomCaller(db, (await register(db, "room-a")).body.token, ["huddles"]))!;
  await call(db, caller, "s2")("claim", { subject: "tone", content: "Keep replies informal here." });
  await defrag(new D1Store(db), DEFAULT_POLICY, null);
  const live = (await rows(db)).filter((r) => r.status === "accepted");
  assert.equal(live.length, 1);
  assert.equal(live[0].scope, "room");
  assert.equal(live[0].project, null);
});

test("a room is offered only its own tools", async () => {
  const db = await setup();
  const { body } = await register(db, "room-a");
  const caller = (await roomCaller(db, body.token, ["huddles"]))!;
  const req = new Request("https://k/mcp", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) });
  const { result } = await (await handleMcp(req, db, caller)).json() as any;
  const tools = result.tools.map((t: any) => t.name);
  assert.deepEqual(tools, ["briefing", "recall", "claim", "expand"]);
  const claim = result.tools.find((t: any) => t.name === "claim");
  assert.deepEqual(Object.keys(claim.inputSchema.properties).sort(), ["confidence", "content", "share_to", "subject", "type"]);
  assert.equal((await call(db, caller, "s1")("correct", { subject: "x", content: "y" })).error, true);
});

test("a room recall with no words finds nothing rather than browsing", async () => {
  const db = await setup();
  const a = await roomClient(db, "room-a");
  await a("claim", { subject: "tone", content: "Keep replies informal here." });
  assert.doesNotMatch((await a("recall", { query: "🙂 ?!" })).text, /tone/);
});

test("bots in one room share its lane, and removing one leaves the others", async () => {
  const db = memoryD1();
  const seat = async (member: string, links?: string[]) =>
    await (await handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id: "room-a", member, ...(links ? { links } : {}) }) }), db, "huddles", { canLink: true })).json() as any;
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('huddles','','t')").run();
  const a = await seat("bot-a", ["huddles"]), b = await seat("bot-b");
  assert.deepEqual(a.links, ["huddles"]);
  assert.equal(b.links, undefined);
  assert.equal(((await db.prepare("SELECT COUNT(*) AS n FROM room_links").first()) as any).n, 1);
  assert.deepEqual(await roomCaller(db, a.token, ["huddles"]), await roomCaller(db, b.token, ["huddles"]));
  await handleRooms(new Request("https://k/rooms/room-a/members/bot-a", { method: "DELETE" }), db, "huddles", { canLink: true });
  assert.equal(await roomCaller(db, a.token, ["huddles"]), null);
  assert.ok(await roomCaller(db, b.token, ["huddles"]));
  assert.equal((await handleRooms(new Request("https://k/rooms/room-a/oops/bot-a", { method: "DELETE" }), db, "huddles", { canLink: true })).status, 400);
});
