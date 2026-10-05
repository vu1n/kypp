import assert from "node:assert/strict";
import { test } from "node:test";
import { type Claim, briefingOrder, compactLine, ftsQuery, planConsolidation, survivor } from "../src/memory.ts";

let n = 0;
function claim(over: Partial<Claim>): Claim {
  n++;
  return {
    id: n.toString(16).padStart(32, "0"), type: "fact", subject: "s", content: "c", scope: "project",
    project: "p", agent: null, user: null, status: "candidate", authority: "agent", confidence: 0.7,
    source_ids: [], code_refs: [], created_at: "2026-10-05", updated_at: `2026-10-05T00:00:0${n % 10}`, ...over,
  };
}

test("authority outranks confidence and acceptance", () => {
  const human = claim({ authority: "human", confidence: 0.1 });
  const agent = claim({ status: "accepted", confidence: 0.99 });
  assert.equal(survivor([agent, human]).id, human.id);
});

test("one session claiming twice is not corroboration", () => {
  const a = claim({ source_ids: ["session:A"] });
  const b = claim({ source_ids: ["session:A"] });
  const plan = planConsolidation([a, b]);
  assert.deepEqual(plan.promote, []);
  assert.equal(plan.supersede.length, 1);
});

test("two sessions agreeing promote the survivor and supersede the rest", () => {
  const a = claim({ source_ids: ["session:A"], confidence: 0.6 });
  const b = claim({ source_ids: ["session:B"], confidence: 0.8 });
  const plan = planConsolidation([a, b]);
  assert.deepEqual(plan.promote, [b.id]);
  assert.deepEqual(plan.supersede, [a.id]);
});

test("same subject in different projects does not group", () => {
  const plan = planConsolidation([claim({ project: "x", source_ids: ["session:A"] }), claim({ project: "y", source_ids: ["session:B"] })]);
  assert.deepEqual(plan, { supersede: [], promote: [] });
});

test("briefing puts pitfalls, then decisions, then procedures first", () => {
  const out = briefingOrder([claim({ type: "fact" }), claim({ type: "procedure" }), claim({ type: "pitfall" }), claim({ type: "decision" })], 3);
  assert.deepEqual(out.map((c) => c.type), ["pitfall", "decision", "procedure"]);
});

test("compact line carries handle, marks and a clipped body", () => {
  const c = claim({ status: "accepted", authority: "human", content: "x".repeat(300), code_refs: [{ path: "a.py" }] });
  const line = compactLine(c);
  assert.ok(line.startsWith(`${c.id.slice(0, 8)} [fact ✓0.7 👤]`), line);
  assert.ok(line.includes("expand") && line.endsWith("→ a.py"), line);
});

test("fts query quotes words so FTS syntax can't break the parse", () => {
  assert.equal(ftsQuery('libkrun OR "rebuild" -x NEAR('), '"libkrun" OR "OR" OR "rebuild" OR "x" OR "NEAR"');
  assert.equal(ftsQuery("  ?? "), "");
});
