// Read-only static tokens (KYPP_API_TOKENS "name!ro:token"): briefing, recall and expand work as for
// any token, and so does feedback (it rates reads, never memory); claim and correct are hidden from tools/list and refused on tools/call.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { apiTokens } from "../src/tokens.ts";
import { memoryD1 } from "./d1.ts";

const SECRET = "x".repeat(20);
const OWNER: Caller = { user: "owner", agent: "claude" };
const REVIEWER: Caller = { user: "owner", agent: "reviewer", readOnly: true };

async function rpc(db: D1Database, caller: Caller, method: string, params: Record<string, unknown> = {}) {
  const req = new Request("https://k/mcp", { method: "POST", headers: { "Mcp-Session-Id": "s1", "X-Kypp-Project": "kypp" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) });
  return ((await (await handleMcp(req, db, caller, { s1: null })).json()) as any).result;
}
const tool = async (db: D1Database, caller: Caller, name: string, args: Record<string, unknown> = {}) => {
  const r = await rpc(db, caller, "tools/call", { name, arguments: args });
  return { text: r.content[0].text as string, error: !!r.isError };
};
const count = async (db: D1Database) => ((await db.prepare("SELECT COUNT(*) AS n FROM memory_claims").first()) as any).n;

test("!ro marks a token read-only, with or without a pin; other tokens are unchanged", () => {
  const m = apiTokens(`huddles:${SECRET},review!ro:${SECRET}a,ci@kypp|brief!ro:${SECRET}b`);
  assert.deepEqual(m.get(SECRET), { name: "huddles" });
  assert.deepEqual(m.get(`${SECRET}a`), { name: "review", readOnly: true });
  assert.deepEqual(m.get(`${SECRET}b`), { name: "ci", projects: ["kypp", "brief"], readOnly: true });
});

test("a read-only token reads memory but can't see or call claim and correct", async () => {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-05T00:00:00Z')").run();
  const id = (await tool(db, OWNER, "claim", { subject: "wrangler port", content: "wrangler dev listens on 8787" })).text.split("\n")[0];

  const names = (await rpc(db, REVIEWER, "tools/list")).tools.map((t: any) => t.name);
  assert.deepEqual(names.sort(), ["briefing", "expand", "feedback", "recall"]);
  assert.doesNotMatch((await rpc(db, REVIEWER, "initialize", { protocolVersion: "2025-06-18" })).instructions, /claim`/);

  assert.match((await tool(db, REVIEWER, "recall", { query: "wrangler", include_candidates: true })).text, /wrangler port/);
  assert.match((await tool(db, REVIEWER, "expand", { handle: id })).text, /8787/);

  const before = await count(db);
  for (const name of ["claim", "correct"]) {
    const out = await tool(db, REVIEWER, name, { subject: "s", content: "a lesson" });
    assert.ok(out.error, `${name} is refused`);
    assert.match(out.text, /read-only/);
  }
  assert.equal(await count(db), before, "nothing is written");
});
