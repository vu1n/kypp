// Agents check memory as they read it: a briefing asks for a check of an accepted claim that is read
// often and hasn't held up in anyone's work lately, and a writable `used` takes it back off the list.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { memoryD1 } from "./d1.ts";

const OWNER: Caller = { user: "owner", agent: "claude" };
const HEADING = "\n# check these if your work touches them, then rate them with `feedback`\n";

async function setup() {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-06T00:00:00Z')").run();
  return db;
}

function agent(db: D1Database, session: string) {
  return async (name: string, args: Record<string, unknown> = {}) => {
    const req = new Request("https://k/mcp", { method: "POST", headers: { "X-Kypp-Project": "kypp", "Mcp-Session-Id": session },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    return ((await (await handleMcp(req, db, OWNER, { s1: null })).json()) as any).result.content[0].text as string;
  };
}

test("an often-read claim nobody has confirmed lately moves to the check section until a used", async () => {
  const db = await setup();
  const w = agent(db, "w");
  const id = (await w("correct", { subject: "wrangler port", content: "wrangler dev listens on 8787", type: "pitfall" })).split("\n")[0].trim();
  await w("correct", { subject: "deploys", content: "the worker deploys on merge to main", type: "procedure" });
  for (let i = 0; i < 9; i++) await agent(db, `r${i}`)("briefing");

  const before = await agent(db, "r9")("briefing");
  assert.ok(!before.includes(HEADING), "9 reads each is under the bar");
  const after = await agent(db, "r10")("briefing");
  const [settled, check] = after.split(HEADING);
  assert.match(check, /wrangler port/);
  assert.match(check, /deploys/);
  assert.doesNotMatch(settled, /wrangler port|deploys/);

  await agent(db, "r10")("feedback", { ratings: [{ handle: id, verdict: "used", note: "ran wrangler dev; it answered on 8787" }] });
  const [settled2, check2] = (await agent(db, "r11")("briefing")).split(HEADING);
  assert.match(settled2, /wrangler port/);
  assert.doesNotMatch(check2, /wrangler port/);
  assert.match(check2, /deploys/);
});

test("a briefing asks for at most two checks", async () => {
  const db = await setup();
  const w = agent(db, "w");
  for (const s of ["a", "b", "c"]) await w("correct", { subject: `trap ${s}`, content: `lesson ${s}`, type: "pitfall" });
  for (let i = 0; i < 10; i++) await agent(db, `r${i}`)("briefing");
  const check = (await agent(db, "x")("briefing")).split(HEADING)[1];
  assert.equal(check.split("\n").filter((l) => /^[0-9a-f]{8} \[/.test(l)).length, 2);
});
