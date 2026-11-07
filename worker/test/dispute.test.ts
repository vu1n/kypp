// A claim a writable session rated `wrong` leaves the briefing until a later `used` clears it or a new
// version replaces it; recall still shows it, marked, and expand shows the note.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { memoryD1 } from "./d1.ts";

const OWNER: Caller = { user: "owner", agent: "claude" };

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

// An accepted claim, as a human correction lands it.
async function accepted(db: D1Database, subject: string, content: string) {
  return (await agent(db, "w")("correct", { subject, content, type: "pitfall" })).split("\n")[0].trim();
}

const WRONG = { verdict: "wrong", note: "the port moved to 8788" };
const USED = { verdict: "used", note: "ran wrangler dev; it answered on 8787" };

test("a wrong rating takes the claim out of the briefing; recall marks it and expand says why", async () => {
  const db = await setup();
  const id = await accepted(db, "wrangler port", "wrangler dev listens on 8787");
  const r = agent(db, "r");
  assert.match(await r("briefing"), /wrangler port/);
  await r("feedback", { ratings: [{ handle: id, ...WRONG }] });

  const next = agent(db, "n");
  assert.doesNotMatch(await next("briefing"), /wrangler port/);
  assert.match(await next("recall", { query: "wrangler port" }), new RegExp(`wrangler port .*⚠ rated wrong \\(expand ${id.slice(0, 8)} for why\\)`));
  const full = JSON.parse(await next("expand", { handle: id }));
  assert.equal(full.rated_wrong.note, "the port moved to 8788");
  assert.equal(full.status, "accepted", "a rating changes no claim");
});

test("a later used from another session clears the dispute", async () => {
  const db = await setup();
  const id = await accepted(db, "wrangler port", "wrangler dev listens on 8787");
  const a = agent(db, "a"), b = agent(db, "b");
  await a("briefing"); await b("briefing");
  await a("feedback", { ratings: [{ handle: id, ...WRONG }] });
  assert.doesNotMatch(await agent(db, "x")("briefing"), /wrangler port/);
  await b("feedback", { ratings: [{ handle: id, ...USED }] });
  assert.match(await agent(db, "y")("briefing"), /wrangler port/);
  assert.doesNotMatch(await agent(db, "y")("recall", { query: "wrangler port" }), /rated wrong/);
});

test("a used made before the wrong doesn't clear it, and the rater withdrawing does", async () => {
  const db = await setup();
  const id = await accepted(db, "wrangler port", "wrangler dev listens on 8787");
  const a = agent(db, "a"), b = agent(db, "b");
  await a("briefing"); await b("briefing");
  await b("feedback", { ratings: [{ handle: id, ...USED }] });
  await a("feedback", { ratings: [{ handle: id, ...WRONG }] });
  assert.doesNotMatch(await agent(db, "x")("briefing"), /wrangler port/);
  await a("feedback", { ratings: [{ handle: id, verdict: "not_used" }] });
  assert.match(await agent(db, "y")("briefing"), /wrangler port/);
});

test("a wrong from a caller that can't write at the claim's level hides nothing", async () => {
  const db = await setup();
  const id = await accepted(db, "wrangler port", "wrangler dev listens on 8787");
  const ro = agent(db, "ro", { ...OWNER, readOnly: true });
  await ro("briefing");
  await ro("feedback", { ratings: [{ handle: id, ...WRONG }] });
  assert.match(await agent(db, "x")("briefing"), /wrangler port/);
});

test("a new version under the subject is not disputed by the old one's rating", async () => {
  const db = await setup();
  const id = await accepted(db, "wrangler port", "wrangler dev listens on 8787");
  const a = agent(db, "a");
  await a("briefing");
  await a("feedback", { ratings: [{ handle: id, ...WRONG }] });
  await accepted(db, "wrangler port", "wrangler dev listens on 8788");
  const brief = await agent(db, "x")("briefing");
  assert.match(brief, /wrangler port — wrangler dev listens on 8788/);
  assert.doesNotMatch(brief, /rated wrong/);
});
