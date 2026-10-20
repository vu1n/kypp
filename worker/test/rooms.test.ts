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

const CONFIGURED = new Map([["huddles", new Set(["d-huddles"])]]);
const NONE = new Map<string, Set<string>>();

const TS = "2026-10-14T00:00:00Z";

async function setup() {
  const db = memoryD1();
  for (const p of ["huddles", "pillbox"]) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(p, "", TS).run();
  }
  return db;
}

async function register(db: D1Database, id: string, links: string[] = [], issuer = "huddles") {
  const res = await handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id, links }) }), db, issuer, { canLink: true, credential: "d-huddles" });
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
  const caller = await roomCaller(db, body.token, CONFIGURED);
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
  assert.deepEqual(await roomCaller(db, body.token, CONFIGURED), { user: "room:huddles:room-a", agent: "room:huddles:room-a", room: "huddles:room-a" });
  assert.equal(await roomCaller(db, "kypr_nope", CONFIGURED), null);
});

test("links must be registered projects", async () => {
  const db = await setup();
  const { status, body } = await register(db, "room-a", ["huddles", "secret-repo"]);
  assert.equal(status, 400);
  assert.match(body.error, /secret-repo/);
});

test("an owner/repo link links the repo only under that owner's org", async () => {
  const db = await setup();
  for (const org of ["vu1n", "acme"]) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(org, "", TS).run();
  }
  await db.prepare("INSERT INTO project_parents(project, parent, created_at) VALUES('huddles','vu1n',?)").bind(TS).run();
  const linked = await register(db, "room-a", ["Vu1n/Huddles", "pillbox"]);
  assert.equal(linked.status, 201);
  assert.deepEqual(linked.body.links, ["huddles", "pillbox"]);
  // Another owner's repo of the same name is not the registered project.
  const other = await register(db, "room-b", ["acme/huddles"]);
  assert.equal(other.status, 400);
  assert.match(other.body.error, /not registered projects: acme\/huddles$/);
  assert.equal((await register(db, "room-c", ["vu1n/pillbox"])).status, 400);
  // The owner is the org at the top of the roll-up, through a group in between, never the group.
  for (const [project, parent] of [["site", "web"], ["web", "vu1n"]]) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(project, "", TS).run();
    await db.prepare("INSERT INTO project_parents(project, parent, created_at) VALUES(?,?,?)").bind(project, parent, TS).run();
  }
  assert.deepEqual((await register(db, "room-f", ["vu1n/site"])).body.links, ["site"]);
  assert.equal((await register(db, "room-g", ["web/site"])).status, 400);
  // Of repos whose names differ only in case, the owner picks the one under its org.
  for (const [project, parent] of [["API", "vu1n"], ["api", "acme"]]) {
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind(project, "", TS).run();
    await db.prepare("INSERT INTO project_parents(project, parent, created_at) VALUES(?,?,?)").bind(project, parent, TS).run();
  }
  assert.deepEqual((await register(db, "room-j", ["acme/api"])).body.links, ["api"]);
  assert.deepEqual((await register(db, "room-k", ["vu1n/api"])).body.links, ["API"]);
  // A project registered under its path form is linked by that name, as before.
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('vu1n/kypp','',?)").bind(TS).run();
  assert.deepEqual((await register(db, "room-h", ["vu1n/kypp"])).body.links, ["vu1n/kypp"]);
  assert.deepEqual((await register(db, "room-i", ["Vu1n/Kypp"])).body.links, ["vu1n/kypp"]);
  // A paired app is refused any link before kypp looks a name up, so it learns nothing from it.
  for (const link of ["vu1n/huddles", "acme/huddles"]) {
    const paired = await handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id: "room-p", links: [link] }) }), db, "app", { canLink: false, grant: "g" });
    assert.equal(paired.status, 403);
  }
  // A pinned issuer's pin bounds the project a qualified link resolves to.
  const pinned = (id: string, linkable: string[]) =>
    handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id, links: ["vu1n/huddles"] }) }), db, "huddles", { canLink: true, credential: "d-huddles", linkable });
  assert.equal((await pinned("room-d", ["huddles"])).status, 201);
  assert.equal((await pinned("room-e", ["pillbox"])).status, 403);
});

test("re-registering rotates the token; a revoked room stays revoked", async () => {
  const db = await setup();
  const first = (await register(db, "room-a")).body.token;
  const again = await register(db, "room-a", ["pillbox"]);
  assert.equal(again.status, 200);
  assert.equal(await roomCaller(db, first, CONFIGURED), null);
  assert.ok(await roomCaller(db, again.body.token, CONFIGURED));
  await handleRooms(new Request("https://k/rooms/room-a", { method: "DELETE" }), db, "huddles", { canLink: true, credential: "d-huddles" });
  assert.equal(await roomCaller(db, again.body.token, CONFIGURED), null);
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

test("a share lands only if the room is still linked when it is written", async () => {
  const db = await setup();
  const { body } = await register(db, "room-a", ["huddles"]);
  const caller = (await roomCaller(db, body.token, CONFIGURED))!;
  // The manager unlinks the repo while System One judges the draft.
  const s1 = { systemOne: async () => {
    await register(db, "room-a", []);
    return { answers: {} };
  } };
  const req = new Request("https://k/mcp", { method: "POST", headers: { "Mcp-Session-Id": "s1" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call",
      params: { name: "claim", arguments: { subject: "release notes", content: "Release notes list user-visible changes first.", share_to: "huddles" } } }) });
  const { result } = await (await handleMcp(req, db, caller, { s1 })).json() as any;
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /no longer linked to huddles/);
  assert.deepEqual(await rows(db), []);
  assert.deepEqual((await db.prepare("SELECT * FROM claims_fts").all()).results, []);
});

test("defrag promotes a lesson inside a lane but never moves it out", async () => {
  const db = await setup();
  const a1 = await roomClient(db, "room-a");
  await a1("claim", { subject: "tone", content: "Keep replies informal here." });
  const caller = (await roomCaller(db, (await register(db, "room-a")).body.token, CONFIGURED))!;
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
  const caller = (await roomCaller(db, body.token, CONFIGURED))!;
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
    await (await handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id: "room-a", member, ...(links ? { links } : {}) }) }), db, "huddles", { canLink: true, credential: "d-huddles" })).json() as any;
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('huddles','','t')").run();
  const a = await seat("bot-a", ["huddles"]), b = await seat("bot-b");
  assert.deepEqual(a.links, ["huddles"]);
  assert.equal(b.links, undefined);
  assert.equal(((await db.prepare("SELECT COUNT(*) AS n FROM room_links").first()) as any).n, 1);
  assert.deepEqual(await roomCaller(db, a.token, CONFIGURED), await roomCaller(db, b.token, CONFIGURED));
  await handleRooms(new Request("https://k/rooms/room-a/members/bot-a", { method: "DELETE" }), db, "huddles", { canLink: true, credential: "d-huddles" });
  assert.equal(await roomCaller(db, a.token, CONFIGURED), null);
  assert.ok(await roomCaller(db, b.token, CONFIGURED));
  assert.equal((await handleRooms(new Request("https://k/rooms/room-a/oops/bot-a", { method: "DELETE" }), db, "huddles", { canLink: true, credential: "d-huddles" })).status, 400);
});

test("a seat with an expiry stops working once it passes", async () => {
  const db = memoryD1();
  const seat = (expires_at: unknown) =>
    handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id: "room-a", member: "bot-a", expires_at }) }), db, "huddles", { canLink: true, credential: "d-huddles" });
  assert.equal((await seat("2020-01-01T00:00:00.000Z")).status, 400);
  assert.equal((await seat("tomorrow")).status, 400);
  const token = (await (await seat("2999-01-01T00:00:00.000Z")).json() as any).token;
  assert.ok(await roomCaller(db, token, CONFIGURED));
  await db.prepare("UPDATE room_members SET expires_at = '2020-01-01T00:00:00.000Z'").run();
  assert.equal(await roomCaller(db, token, CONFIGURED), null);
});

test("a configured issuer's seat stops with the secret that made it, and a new secret never revives it", async () => {
  const db = memoryD1();
  const seat = async (credential?: string) =>
    (await (await handleRooms(new Request("https://k/rooms", { method: "POST", body: JSON.stringify({ id: "room-a", member: "bot-a" }) }), db, "huddles",
      { canLink: true, ...(credential ? { credential } : {}) })).json() as any).token as string;
  const issuers = (...secrets: string[]) => new Map([["huddles", new Set(secrets)]]);
  const old = await seat("d-old");
  assert.ok(await roomCaller(db, old, issuers("d-old")));
  // While a rotation overlaps, both secrets are listed and the old seat still works.
  assert.ok(await roomCaller(db, old, issuers("d-old", "d-new")));
  // The name removed, then listed again under a new secret: the old seat stays out.
  assert.equal(await roomCaller(db, old, NONE), null);
  assert.equal(await roomCaller(db, old, issuers("d-new")), null);
  // Seating the bot again under the new secret is what brings it back.
  assert.ok(await roomCaller(db, await seat("d-new"), issuers("d-new")));
  // A seat with no recorded secret (made before seats recorded one) never signs in.
  assert.equal(await roomCaller(db, await seat(), issuers("d-new")), null);
});

test("a busy linked room's briefing still lists its org's rules", async () => {
  const db = await setup();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('vu1n','',?)").bind(TS).run();
  await db.prepare("INSERT INTO project_parents(project, parent, created_at) VALUES('huddles','vu1n',?)").bind(TS).run();
  const put = async (i: number, scope: string, project: string, subject: string) => {
    const id = i.toString(16).padStart(32, "e");
    await db.prepare("INSERT INTO memory_claims(id,type,subject,content,scope,project,status,confidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)")
      .bind(id, "fact", subject, `${subject} lesson`, scope, project, "accepted", 0.9, TS, TS).run();
    await db.prepare("INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)").bind(id, subject, `${subject} lesson`).run();
  };
  // More settled repo claims than the briefing's capped pool holds, all ranked above the org.
  for (let i = 1; i <= 40; i++) await put(i, "project", "huddles", `repo rule ${i}`);
  await put(99, "group", "vu1n", "draft PRs first");
  const out = (await (await roomClient(db, "room-a", ["huddles"]))("briefing")).text;
  assert.match(out, /^# how this org works\n.*draft PRs first/);
});
