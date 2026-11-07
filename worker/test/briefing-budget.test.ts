// The briefing's claim lines share one budget: full lines while it lasts, then subject-only lines,
// then a count of what was left out. Only what the reply shows is recorded as shown.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Caller, handleMcp } from "../src/mcp.ts";
import { BRIEFING_CHARS, type Claim, compactLine, fitBriefing, shortLine } from "../src/memory.ts";
import { memoryD1 } from "./d1.ts";

const OWNER: Caller = { user: "owner", agent: "claude" };

const claim = (id: string, content: string): Claim => ({
  id, type: "pitfall", subject: `subject ${id}`, content, scope: "project", project: "kypp", agent: null, user: null,
  status: "accepted", authority: "human", confidence: 1, source_ids: [], code_refs: [], created_at: "", updated_at: "",
});

test("fitBriefing keeps full lines, then short ones, then leaves the rest out", () => {
  const cs = ["a", "b", "c", "d"].map((id) => claim(id.repeat(8), "x".repeat(200)));
  const [full, short] = [compactLine(cs[0]).length + 1, shortLine(cs[0]).length + 1];
  const fit = fitBriefing(cs, 2 * full + short + short - 1);
  assert.deepEqual([...fit.values()], ["full", "full", "short"]);
  assert.equal(fit.has("dddddddd"), false);
});

test("a briefing over budget shortens and then drops its last lines, and records only what it shows", async () => {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-06T00:00:00Z')").run();
  const call = async (session: string, name: string, args: Record<string, unknown>) => {
    const req = new Request("https://k/mcp", { method: "POST", headers: { "X-Kypp-Project": "kypp", "Mcp-Session-Id": session },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
    return ((await (await handleMcp(req, db, OWNER, { s1: null })).json()) as any).result.content[0].text as string;
  };
  for (let i = 0; i < 30; i++) await call("w", "correct", { subject: `trap ${i}`, content: `${i} `.repeat(110), type: "pitfall" });

  const out = await call("r", "briefing", { limit: 30 });
  const lines = out.split("\n").filter((l) => /^[0-9a-f]{8} \[/.test(l));
  assert.ok(lines.reduce((n, l) => n + l.length + 1, 0) <= BRIEFING_CHARS);
  assert.match(out, /\(expand for the lesson\)/);
  const left = Number(/\n\((\d+) more left out to keep the briefing short; `recall` finds them\)$/.exec(out)?.[1]);
  assert.equal(lines.length + left, 30);
  const shown = await db.prepare("SELECT COUNT(*) AS n FROM claim_usages WHERE consumer = 'r' AND surface = 'briefing'").first<{ n: number }>();
  assert.equal(shown!.n, lines.length);
});

test("a briefing under budget is unchanged", async () => {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp', '', '2026-11-06T00:00:00Z')").run();
  const req = (args: Record<string, unknown>, name: string) => new Request("https://k/mcp", { method: "POST", headers: { "X-Kypp-Project": "kypp", "Mcp-Session-Id": "s" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }) });
  await handleMcp(req({ subject: "port", content: "wrangler dev listens on 8787", type: "pitfall" }, "correct"), db, OWNER, { s1: null });
  const out = ((await (await handleMcp(req({}, "briefing"), db, OWNER, { s1: null })).json()) as any).result.content[0].text as string;
  assert.match(out, /^# pitfall\n[0-9a-f]{8} \[pitfall ✓0\.9 @kypp\] port — wrangler dev listens on 8787$/);
});
