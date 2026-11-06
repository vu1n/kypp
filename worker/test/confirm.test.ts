// A `used` rating from another session confirms a candidate: consolidate counts it like a second
// session's claim, while a re-claim of what the session was shown (an echo) still counts for nothing.
import assert from "node:assert/strict";
import { test } from "node:test";
import { consolidate, plan } from "../src/consolidate.ts";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { type Claim, type Policy, recurs } from "../src/memory.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const OWNER: Caller = { user: "owner", agent: "claude" };
const NOW: Policy = { k: 2, minGapMs: 0, autoAccept: () => true };

async function setup() {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-06T00:00:00Z')").run();
  return db;
}

function agent(db: D1Database, session: string, caller: Caller = OWNER) {
  return async (name: string, args: Record<string, unknown> = {}) => {
    const req = new Request("https://k/mcp", { method: "POST", headers: { "X-Kypp-Project": "kypp", "Mcp-Session-Id": session },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, caller, { s1: null })).json() as any;
    return result.content[0].text as string;
  };
}

const status = async (db: D1Database, id: string) =>
  ((await db.prepare("SELECT status FROM memory_claims WHERE id = ?").bind(id).first()) as any).status as string;

const LESSON = { subject: "wrangler port", content: "wrangler dev listens on 8787" };
const USED = { verdict: "used", note: "ran wrangler dev; it answered on 8787" };

test("a reader that re-claims what it was shown is told it's an echo; its used rating promotes the claim", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });

  const echo = await r("claim", LESSON);
  assert.match(echo, /\necho: .*doesn't count toward accepting it.*`used`/);
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate", "an echo is not a second session");

  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "accepted");
});

test("a used rating counts only from another, writable session, and only while it is the newest verdict", async () => {
  const db = await setup(), store = new D1Store(db);
  const w = agent(db, "w");
  const id = (await w("claim", LESSON)).split("\n")[0];

  await w("recall", { query: "wrangler port" });
  await w("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate", "the writer can't confirm its own claim");

  const ro = agent(db, "ro", { ...OWNER, readOnly: true });
  await ro("recall", { query: "wrangler port" });
  await ro("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate", "a read-only token can't write memory, so its rating doesn't confirm");

  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await r("feedback", { ratings: [{ handle: id, verdict: "not_used" }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate", "a later not_used withdraws the used, even within the same millisecond");

  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "accepted");
});

test("within one call, the last rating of a claim is the verdict", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  await r("feedback", { ratings: [{ handle: id, ...USED }, { handle: id, verdict: "not_used" }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate");
  await r("feedback", { ratings: [{ handle: id, verdict: "not_used" }, { handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "accepted");
});

test("a rating stored without the writable mark never confirms", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  await agent(db, "r")("recall", { query: "wrangler port" });
  await db.prepare("INSERT INTO claim_usages(id,consumer,claim_id,project,scope,surface,score,metadata,created_at)"
    + " VALUES('legacy','r',?,'kypp','project','feedback',1,?,?)").bind(id, JSON.stringify(USED), new Date().toISOString()).run();
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate");
});

test("a used rating withdrawn after planning stops the promotion it supported", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  const plans = await plan(store, NOW, null);
  assert.deepEqual(plans.flatMap((p) => p.promote), [id]);
  await r("feedback", { ratings: [{ handle: id, verdict: "wrong", note: "it listens on 8788 now" }] });
  assert.deepEqual(await store.apply(plans), { applied: 0, stale: 1 });
  assert.equal(await status(db, id), "candidate");
});

test("a confirmation is timed by its rating, so the recurrence gap still applies", () => {
  const claim = { id: "a", session: "w", source_ids: ["session:w"], created_at: "2026-11-06T10:00:00Z" } as Claim;
  const hour = 3600_000;
  assert.equal(recurs([{ ...claim, confirmations: [{ session: "r", at: "2026-11-06T10:30:00Z" }] }], 2, hour), false);
  assert.equal(recurs([{ ...claim, confirmations: [{ session: "r", at: "2026-11-06T11:00:00Z" }] }], 2, hour), true);
  assert.equal(recurs([{ ...claim, confirmations: [{ session: "w", at: "2026-11-07T10:00:00Z" }] }], 2, hour), false);
});

test("a session's newest rating decides even when it switched to a read-only token", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r"), rRo = agent(db, "r", { ...OWNER, readOnly: true });
  await r("recall", { query: "wrangler port" });
  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  const plans = await plan(store, NOW, null);
  await rRo("feedback", { ratings: [{ handle: id, verdict: "wrong", note: "it listens on 8788 now" }] });
  assert.deepEqual(await store.apply(plans), { applied: 0, stale: 1 }, "the fence sees the read-only withdrawal");
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate", "and so does the next pass");
});

test("an echo never retires the stamped claim it echoes, so rating that claim later still confirms", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  const echo = (await r("claim", { ...LESSON, content: "wrangler dev listens on 8787 unless --port is set", confidence: 0.9 })).split("\n")[0];
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate", "the higher-confidence echo didn't supersede it");
  assert.equal(await status(db, echo), "candidate");

  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, echo), "accepted", "the newest, strongest wording wins once the subject is confirmed");
  assert.equal(await status(db, id), "superseded");
});

test("a rating confirms only for a caller that could write at the claim's level, whatever the session saw", async () => {
  const db = await setup(), store = new D1Store(db);
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('other', '', '2026-11-06T00:00:00Z')").run();
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  // One session id carried across two credentials: a read-only one that can see kypp, and a writer pinned to other.
  await agent(db, "s", { ...OWNER, readOnly: true })("recall", { query: "wrangler port" });
  const out = await agent(db, "s", { user: "bot", agent: "bot", projects: ["other"] })("feedback", { ratings: [{ handle: id, ...USED }] });
  assert.equal(out, "recorded 1 rating");
  const row = await db.prepare("SELECT metadata FROM claim_usages WHERE surface = 'feedback'").first() as any;
  assert.equal(JSON.parse(row.metadata).writable, undefined);
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "candidate");

  await agent(db, "s", { user: "bot", agent: "bot", projects: ["kypp"] })("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "accepted", "a writer pinned to kypp may confirm it");
});

test("a feedback-backed promotion still waits when the S1 judge finds the claims conflicted", async () => {
  const s1 = { systemOne: async ({ state }: any) =>
    ({ answers: { agree: { type: "noul", noul: state.claims.some((c: any) => c.content.includes("8788")) ? 0.1 : 0.9 } } }) } as any;
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  const echo = (await r("claim", { ...LESSON, content: "wrangler dev listens on 8788", confidence: 0.9 })).split("\n")[0];
  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, s1);
  assert.deepEqual([await status(db, id), await status(db, echo)], ["candidate", "candidate"],
    "held: nothing promotes, and the echo doesn't retire the rated claim");
});

test("a room confirms claims in the repos it is linked to, and not in others", async () => {
  const db = await setup(), store = new D1Store(db);
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('other', '', '2026-11-06T00:00:00Z')").run();
  await db.prepare("INSERT INTO room_links(room, project, created_at) VALUES('huddles:r', 'kypp', '2026-11-06T00:00:00Z')").run();
  const room: Caller = { user: "room:huddles:r", agent: "room:huddles:r", room: "huddles:r" };
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const otherId = (await agent(db, "w2")("claim", { ...LESSON, project: "other" })).split("\n")[0];
  // The room session is shown both (the second through a read-only token carrying the same session id).
  const bot = agent(db, "rs", room);
  await bot("recall", { query: "wrangler port", include_candidates: true });
  await agent(db, "rs", { ...OWNER, readOnly: true })("recall", { query: "wrangler port", project: "other", include_candidates: true });
  assert.equal(await bot("feedback", { ratings: [{ handle: id, ...USED }, { handle: otherId, ...USED }] }), "recorded 2 ratings");
  await consolidate(store, NOW, null);
  assert.equal(await status(db, id), "accepted", "kypp is linked to the room");
  assert.equal(await status(db, otherId), "candidate", "other isn't");
});

test("a room unlinked between the check and the insert gets no writable mark", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const row = { id, project: "kypp", scope: "project", score: 1, verdict: "used", note: "checked", writable: true };
  await store.recordFeedback("rs", [row], "huddles:r");   // no link: the insert's own check refuses the mark
  await db.prepare("INSERT INTO room_links(room, project, created_at) VALUES('huddles:r', 'kypp', '2026-11-06T00:00:00Z')").run();
  await store.recordFeedback("rs2", [row], "huddles:r");
  const marks = (await db.prepare("SELECT consumer, metadata FROM claim_usages WHERE surface = 'feedback' ORDER BY rowid").all()).results as any[];
  assert.deepEqual(marks.map((m) => [m.consumer, JSON.parse(m.metadata).writable ?? false]), [["rs", false], ["rs2", true]]);
});

test("re-rating a counted used before apply moves its time, so the stale plan is refused", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await db.prepare("UPDATE claim_usages SET created_at = '2026-01-01T00:00:00.000Z' WHERE surface = 'feedback'").run();
  const plans = await plan(store, NOW, null);
  assert.deepEqual(plans.flatMap((p) => p.promote), [id]);
  await r("feedback", { ratings: [{ handle: id, ...USED }] });   // still used, but newer
  assert.deepEqual(await store.apply(plans), { applied: 0, stale: 1 });
});

test("an echo that changes the claim is kept as a pending update, and replaces it once the update is confirmed", async () => {
  const db = await setup(), store = new D1Store(db);
  const id = (await agent(db, "w")("claim", LESSON)).split("\n")[0];
  const r = agent(db, "r");
  await r("recall", { query: "wrangler port" });
  const copy = (await r("claim", { ...LESSON, content: "wrangler dev  listens on 8787" })).split("\n")[0];
  await consolidate(store, NOW, null);
  assert.equal(await status(db, copy), "superseded", "a copy of what it was shown is still dropped");

  const update = (await r("claim", { ...LESSON, content: "wrangler dev listens on 8787; pass --port to change it" })).split("\n")[0];
  await consolidate(store, NOW, null);
  assert.equal(await status(db, update), "candidate", "the change is kept");
  await r("feedback", { ratings: [{ handle: id, ...USED }] });
  await consolidate(store, NOW, null);
  assert.deepEqual([await status(db, id), await status(db, update)], ["accepted", "candidate"], "the confirmed claim promotes; the change waits as its update");

  for (const s of ["s1", "s2"]) {
    const reader = agent(db, s);
    await reader("recall", { query: "wrangler port" });
    await reader("feedback", { ratings: [{ handle: update, verdict: "used", note: "--port 9000 moved it" }] });
  }
  await consolidate(store, NOW, null);
  assert.deepEqual([await status(db, id), await status(db, update)], ["superseded", "accepted"], "two confirmations promote the update");
});
