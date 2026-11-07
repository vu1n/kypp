// A claim can say how it is known (`evidence`): reads mark it apart from a bare assertion, expand
// returns all of it, the write gate reads it with the lesson, and purge erases it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { gatedText } from "../src/memory.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const OWNER: Caller = { user: "owner", agent: "claude" };

async function setup() {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-06T00:00:00Z')").run();
  return db;
}

function agent(db: D1Database, session: string) {
  return async (name: string, args: Record<string, unknown> = {}) => {
    const req = new Request("https://k/mcp", { method: "POST", headers: { "X-Kypp-Project": "kypp", "Mcp-Session-Id": session },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, OWNER, { s1: null })).json() as any;
    return { text: result.content[0].text as string, error: !!result.isError };
  };
}

const HOW = "ran `wrangler dev`; it answered on 8787";

test("a claim with evidence is marked in recall and expands to all of it", async () => {
  const db = await setup();
  const w = agent(db, "w");
  const id = (await w("claim", { subject: "wrangler port", content: "wrangler dev listens on 8787", evidence: `  ${HOW}  ` })).text.split("\n")[0];
  await w("claim", { subject: "deploy target", content: "the worker deploys on merge to main" });

  const lines = (await agent(db, "r")("recall", { query: "wrangler deploys worker port", include_candidates: true })).text;
  assert.match(lines, new RegExp(`wrangler port — wrangler dev listens on 8787 \\[checked: ${HOW.replace(/[`;]/g, ".")}\\]`));
  assert.match(lines, /deploy target — the worker deploys on merge to main$/m, "a bare claim carries no mark");
  const full = JSON.parse((await agent(db, "r")("expand", { handle: id })).text);
  assert.equal(full.evidence, HOW);
});

test("long evidence is clipped on the line and refused past its limit", async () => {
  const db = await setup();
  const w = agent(db, "w");
  const long = "x".repeat(150);
  await w("claim", { subject: "s", content: "a lesson about ports", evidence: long });
  assert.match((await w("recall", { query: "ports", include_candidates: true })).text, /\[checked: x{100}…\]/);
  const out = await w("claim", { subject: "t", content: "another lesson about ports", evidence: "y".repeat(501) });
  assert.equal(out.error, true);
  assert.match(out.text, /keep it to 500/);
});

test("the write gate reads the evidence with the lesson", () => {
  assert.equal(gatedText("lesson", null), "lesson");
  assert.equal(gatedText("lesson", "ran it"), "lesson\n\nHow it is known: ran it");
});

test("purge erases the evidence", async () => {
  const db = await setup();
  const id = (await agent(db, "w")("claim", { subject: "s", content: "token works", evidence: "curl with sk-123 returned 200" })).text.split("\n")[0];
  await new D1Store(db).operatorPurge(id, "leaked key");
  const row = await db.prepare("SELECT metadata FROM memory_claims WHERE id = ?").bind(id).first<{ metadata: string }>();
  assert.doesNotMatch(row!.metadata, /sk-123/);
});
