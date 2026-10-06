import assert from "node:assert/strict";
import { test } from "node:test";
import { type Claim, type Policy, briefingOrder, compactLine, ftsQuery, planConsolidation, planGroup, renderBriefing, survivor } from "../src/memory.ts";

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
  const plan = planGroup([a, b]);
  assert.deepEqual(plan.promote, []);
  assert.equal(plan.supersede.length, 1);
});

test("two sessions agreeing promote the survivor and supersede the rest", () => {
  const a = claim({ source_ids: ["session:A"], confidence: 0.6 });
  const b = claim({ source_ids: ["session:B"], confidence: 0.8 });
  const plan = planGroup([a, b]);
  assert.deepEqual(plan.promote, [b.id]);
  assert.deepEqual(plan.supersede, [a.id]);
});

test("same subject in different projects does not group", () => {
  const plan = planConsolidation([claim({ project: "x", source_ids: ["session:A"] }), claim({ project: "y", source_ids: ["session:B"] })]);
  assert.deepEqual(plan, []);
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

test("user-scope claims group per owner", () => {
  const mine = claim({ scope: "user", project: null, user: "vu", source_ids: ["session:A"] });
  const theirs = claim({ scope: "user", project: null, user: "ana", source_ids: ["session:B"] });
  assert.deepEqual(planConsolidation([mine, theirs]), []);
});

test("briefing renders one heading per type", () => {
  const out = renderBriefing(briefingOrder([claim({ type: "fact" }), claim({ type: "pitfall" }), claim({ type: "pitfall" })], 3));
  assert.deepEqual(out.split("\n").filter((l) => l.startsWith("#")), ["# pitfall", "# fact"]);
  assert.equal(renderBriefing([]), "(no accepted memory yet)");
});

const at = (iso: string) => ({ created_at: iso, updated_at: iso });

test("an update to an accepted answer stays pending instead of being superseded", () => {
  const old = claim({ status: "accepted", confidence: 0.9, ...at("2026-10-01T00:00:00Z") });
  const upd = claim({ source_ids: ["session:A"], ...at("2026-10-02T00:00:00Z") });
  const stale = claim({ source_ids: ["session:B"], ...at("2026-09-30T00:00:00Z") });
  const plan = planGroup([old, upd, stale]);
  assert.deepEqual(plan.promote, []);
  assert.deepEqual(plan.supersede, [stale.id]);
});

test("a recurring update replaces the accepted answer", () => {
  const old = claim({ status: "accepted", confidence: 0.9, ...at("2026-10-01T00:00:00Z") });
  const u1 = claim({ source_ids: ["session:A"], ...at("2026-10-02T00:00:00Z") });
  const u2 = claim({ source_ids: ["session:B"], confidence: 0.8, ...at("2026-10-02T03:00:00Z") });
  const plan = planGroup([old, u1, u2]);
  assert.deepEqual(plan.promote, [u2.id]);
  assert.deepEqual(plan.supersede.sort(), [old.id, u1.id].sort());
});

test("a lower-authority update never replaces a higher-authority answer", () => {
  const human = claim({ status: "accepted", authority: "human", ...at("2026-10-01T00:00:00Z") });
  const a = claim({ source_ids: ["session:A"], ...at("2026-10-02T00:00:00Z") });
  const b = claim({ source_ids: ["session:B"], ...at("2026-10-02T03:00:00Z") });
  const plan = planGroup([human, a, b]);
  assert.deepEqual(plan.promote, []);
  assert.deepEqual(plan.supersede.sort(), [a.id, b.id].sort());
});

const policy = (over: Partial<Policy>): Policy => ({ k: 2, minGapMs: 0, autoAccept: () => true, ...over });

test("recurrence needs the sessions to be apart in time", () => {
  const a = claim({ source_ids: ["session:A"], ...at("2026-10-02T00:00:00Z") });
  const b = claim({ source_ids: ["session:B"], ...at("2026-10-02T00:05:00Z") });
  assert.deepEqual(planGroup([a, b], policy({ minGapMs: 3600_000 })).promote, []);
  const c = claim({ source_ids: ["session:C"], ...at("2026-10-02T02:00:00Z") });
  assert.equal(planGroup([a, b, c], policy({ minGapMs: 3600_000 })).promote.length, 1);
});

test("a held group supersedes duplicates but promotes nothing", () => {
  const a = claim({ scope: "global", project: null, source_ids: ["session:A"] });
  const b = claim({ scope: "global", project: null, source_ids: ["session:B"] });
  assert.deepEqual(planGroup([a, b], policy({ autoAccept: (c) => c.scope === "project" })).promote, []);
});

test("a plan carries the group version it was read at", () => {
  const a = claim({ updated_at: "2026-10-02T00:00:00Z" }), b = claim({ updated_at: "2026-10-03T00:00:00Z" });
  assert.equal(planGroup([a, b]).version, "2:2026-10-03T00:00:00Z");
});

test("compact line survives junk code_refs from old rows", () => {
  assert.doesNotThrow(() => compactLine(claim({ code_refs: [null as any, 3 as any, { path: "x.py" }] })));
});
