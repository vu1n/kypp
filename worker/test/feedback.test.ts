// Agent feedback: a session rates the claims a read showed it (used / not_used / wrong), stored as its
// own claim_usages rows (surface "feedback", score 1 / 0 / -1). It never changes a claim.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const OWNER: Caller = { user: "owner", agent: "claude" };

async function setup() {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-06T00:00:00Z')").run();
  return db;
}

function agent(db: D1Database, session: string | null, caller: Caller = OWNER) {
  return async (name: string, args: Record<string, unknown> = {}) => {
    const headers: Record<string, string> = { "X-Kypp-Project": "kypp" };
    if (session) headers["Mcp-Session-Id"] = session;
    const req = new Request("https://k/mcp", { method: "POST", headers,
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, caller, { s1: null })).json() as any;
    return { text: result.content[0].text as string, error: !!result.isError };
  };
}

const feedbackRows = async (db: D1Database) =>
  (await db.prepare("SELECT consumer, claim_id, project, score, metadata FROM claim_usages WHERE surface = 'feedback' ORDER BY created_at, rowid").all()).results as any[];

test("a session rates what it was shown; each verdict is a feedback row with its score", async () => {
  const db = await setup();
  const writer = agent(db, "w");
  const a = (await writer("claim", { subject: "wrangler port", content: "wrangler dev listens on 8787" })).text.split("\n")[0];
  const b = (await writer("claim", { subject: "deploys", content: "the worker deploys on merge to main" })).text.split("\n")[0];
  const reader = agent(db, "r");
  await reader("recall", { query: "wrangler deploys worker", include_candidates: true });

  const out = await reader("feedback", { ratings: [
    { handle: a.slice(0, 8), verdict: "used", note: "ran wrangler dev; it answered on 8787" },
    { handle: b, verdict: "not_used" },
  ] });
  assert.equal(out.error, false, out.text);
  assert.equal(out.text, "recorded 2 ratings");
  const rows = await feedbackRows(db);
  assert.deepEqual(rows.map((r) => [r.consumer, r.claim_id, r.project, r.score]), [["r", a, "kypp", 1], ["r", b, "kypp", 0]]);
  assert.deepEqual(JSON.parse(rows[0].metadata), { verdict: "used", note: "ran wrangler dev; it answered on 8787", writable: true });
  assert.deepEqual(JSON.parse(rows[1].metadata), { verdict: "not_used", writable: true });

  const claims = (await db.prepare("SELECT status FROM memory_claims").all()).results as any[];
  assert.ok(claims.every((c) => c.status === "candidate"), "feedback changes no claim");
});

test("only claims shown to this session can be rated, and used/wrong need a note", async () => {
  const db = await setup();
  const id = (await agent(db, "w")("claim", { subject: "s", content: "a lesson about ports" })).text.split("\n")[0];
  await agent(db, "other")("recall", { query: "ports", include_candidates: true });

  const r = agent(db, "r");
  const unseen = await r("feedback", { ratings: [{ handle: id, verdict: "used", note: "x" }] });
  assert.match(unseen.text, /recorded 0 ratings\nskipped:\n.*not shown to this session/);

  await r("expand", { handle: id });
  const bare = await r("feedback", { ratings: [
    { handle: id, verdict: "used" }, { handle: id, verdict: "wrong", note: " " }, { handle: id, verdict: "great" }, { handle: id, verdict: "constructor", note: "x" }, { handle: "zz", verdict: "used", note: "x" },
  ] });
  assert.match(bare.text, /^recorded 0 ratings/);
  assert.match(bare.text, /used needs a note/);
  assert.match(bare.text, /wrong needs a note/);
  assert.match(bare.text, /verdict must be used, not_used or wrong/);
  assert.equal(bare.text.match(/verdict must be used, not_used or wrong/g)?.length, 2, "an inherited property name is no verdict");
  assert.match(bare.text, /bad claim handle/);
  assert.equal((await feedbackRows(db)).length, 0);

  const ok = await r("feedback", { ratings: [{ handle: id, verdict: "toString", note: "x" }, { handle: id, verdict: "wrong", note: "the port is 8788 now" }] });
  assert.match(ok.text, /^recorded 1 rating\nskipped:/);
  assert.deepEqual((await feedbackRows(db)).map((x) => x.score), [-1]);
});

test("feedback needs a session and at most 50 ratings", async () => {
  const db = await setup();
  const none = await agent(db, null)("feedback", { ratings: [{ handle: "abcdef12", verdict: "not_used" }] });
  assert.ok(none.error);
  assert.match(none.text, /needs the session id/);
  const r = agent(db, "r");
  assert.match((await r("feedback", { ratings: [] })).text, /at least one/);
  const many = Array.from({ length: 51 }, () => ({ handle: "abcdef12", verdict: "not_used" }));
  assert.match((await r("feedback", { ratings: many })).text, /at most 50/);
});

test("a read-only token can rate what it read", async () => {
  const db = await setup();
  const id = (await agent(db, "w")("claim", { subject: "s", content: "a lesson about ports" })).text.split("\n")[0];
  const ro = agent(db, "ro", { user: "owner", agent: "reviewer", readOnly: true });
  await ro("expand", { handle: id });
  assert.equal((await ro("feedback", { ratings: [{ handle: id, verdict: "not_used" }] })).text, "recorded 1 rating");
});

test("two ratings of one claim in a call: the last one listed is the verdict", async () => {
  const db = await setup();
  const id = (await agent(db, "w")("claim", { subject: "s", content: "a lesson about ports" })).text.split("\n")[0];
  const r = agent(db, "r");
  await r("expand", { handle: id });
  const out = await r("feedback", { ratings: [{ handle: id.slice(0, 8), verdict: "used", note: "worked" }, { handle: id, verdict: "wrong", note: "it did not" }] });
  assert.equal(out.text, "recorded 1 rating");
  assert.deepEqual((await feedbackRows(db)).map((x) => x.score), [-1]);
});

test("feedback after a purge keeps the verdict but drops the note", async () => {
  const db = await setup();
  const id = (await agent(db, "w")("claim", { subject: "s", content: "token sk-123 works" })).text.split("\n")[0];
  const r = agent(db, "r");
  await r("expand", { handle: id });
  await new D1Store(db).operatorPurge(id, "leaked key");
  assert.equal((await r("feedback", { ratings: [{ handle: id, verdict: "wrong", note: "sk-123 leaked" }] })).text, "recorded 1 rating");
  const [row] = await feedbackRows(db);
  assert.deepEqual(JSON.parse(row.metadata), { verdict: "wrong", writable: true });
});

test("purge strips feedback notes on the claim but keeps the verdicts", async () => {
  const db = await setup();
  const id = (await agent(db, "w")("claim", { subject: "s", content: "token sk-123 works" })).text.split("\n")[0];
  const r = agent(db, "r");
  await r("expand", { handle: id });
  await r("feedback", { ratings: [{ handle: id, verdict: "wrong", note: "sk-123 is a leaked key" }] });
  await new D1Store(db).operatorPurge(id, "leaked key");
  const [row] = await feedbackRows(db);
  assert.equal(row.score, -1);
  assert.deepEqual(JSON.parse(row.metadata), { verdict: "wrong", writable: true });
});

test("instructions name feedback for every caller that has it", async () => {
  const db = await setup();
  const init = async (caller: Caller, correct: boolean) => {
    const req = new Request("https://k/mcp", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) });
    return ((await (await handleMcp(req, db, caller, { s1: null, correct })).json()) as any).result.instructions as string;
  };
  const off = await init(OWNER, false);
  assert.match(off, /\n4\. BEFORE YOU FINISH — `feedback`/);
  assert.doesNotMatch(off, /correct\(/);
  assert.match(await init(OWNER, true), /\n4\. A HUMAN GAVE YOU[\s\S]*\n5\. BEFORE YOU FINISH/);
  const ro = await init({ ...OWNER, readOnly: true }, true);
  assert.match(ro, /\n3\. BEFORE YOU FINISH — `feedback`/);
  assert.doesNotMatch(ro, /claim`/);
});
