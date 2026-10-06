import assert from "node:assert/strict";
import { test } from "node:test";
import { consolidate } from "../src/consolidate.ts";
import { planConsolidation } from "../src/memory.ts";
import type { S1Client } from "../src/s1.ts";
import { type ClaimInput, D1Store, cleanCodeRefs } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const base = (over: Partial<ClaimInput>): ClaimInput => ({
  type: "fact", subject: "s", content: "c", scope: "project", project: "p", confidence: 0.7,
  sourceIds: [], codeRefs: [], accept: false, agent: "a", user: "vu", ...over,
});

test("expand can't see another user's user-scope claim, by id or by prefix", async () => {
  const store = new D1Store(memoryD1());
  const theirs = await store.claim(base({ scope: "user", user: "ana", subject: "private", content: "ana's note" }));
  assert.equal(await store.get(theirs, "vu"), null);
  assert.equal((await store.get(theirs, "ana"))?.id, theirs);
  await assert.rejects(store.get(theirs.slice(0, 4), "vu"), /8-32 hex/);
});

test("the ambiguity check only counts claims the caller can see", async () => {
  const store = new D1Store(memoryD1());
  // Two rows sharing an 8-char prefix: one visible, one in someone else's user scope.
  const db = (store as any).db as D1Database;
  const ts = "2026-10-06T00:00:00Z";
  for (const [id, scope, user] of [["abcdef0100000000000000000000000a", "project", "vu"], ["abcdef0100000000000000000000000b", "user", "ana"]]) {
    await db.prepare("INSERT INTO memory_claims(id,type,subject,content,scope,project,user,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)")
      .bind(id, "fact", "s", "c", scope, scope === "project" ? "p" : null, user, "accepted", ts, ts).run();
  }
  assert.equal((await store.get("abcdef01", "vu"))?.id, "abcdef0100000000000000000000000a");
});

test("a stale plan can't undo a correction that landed after it was read", async () => {
  const store = new D1Store(memoryD1());
  await store.claim(base({ subject: "tag", content: "old a", sourceIds: ["session:A"] }));
  await store.claim(base({ subject: "tag", content: "old b", sourceIds: ["session:B"] }));
  const stalePlan = planConsolidation(await store.liveClaims());          // cron reads...
  const fix = await store.claim(base({ subject: "tag", content: "right", accept: true, confidence: 0.95 }));
  await store.apply(planConsolidation(await store.liveClaims({ scope: "project", project: "p", subject: "tag" }))); // correct() consolidates
  assert.deepEqual(await store.apply(stalePlan), { applied: 0, stale: 1 }); // ...cron writes late
  const live = await store.liveClaims({ subject: "tag" });
  assert.deepEqual(live.map((c) => [c.id, c.status]), [[fix, "accepted"]]);
});

test("code_refs are validated before storage", async () => {
  assert.deepEqual(cleanCodeRefs([{ path: "a.py", line: 3 }]), [{ path: "a.py" }]);
  for (const bad of [{ path: "a" }, [null], [[1]], [{ path: 3 }], [{}], new Array(11).fill({ path: "x" })]) {
    assert.throws(() => cleanCodeRefs(bad), /code_ref/);
  }
  const store = new D1Store(memoryD1());
  await assert.rejects(store.claim(base({ codeRefs: [null] })), /code_ref/);
});

test("the S1 judge holds back a conflicted promotion, and only that", async () => {
  const s1: S1Client = { systemOne: async ({ state }: any) =>
    ({ answers: { agree: { type: "noul", noul: state.claims.some((c: any) => c.content.includes("port 2")) ? 0.1 : 0.9 } } }) };
  const store = new D1Store(memoryD1());
  await store.claim(base({ subject: "port", content: "use port 1", sourceIds: ["session:A"] }));
  await store.claim(base({ subject: "port", content: "use port 2", sourceIds: ["session:B"] }));
  await store.claim(base({ subject: "lint", content: "run ruff", sourceIds: ["session:A"] }));
  await store.claim(base({ subject: "lint", content: "run ruff first", sourceIds: ["session:B"] }));
  await consolidate(store, { k: 2, minGapMs: 0, autoAccept: () => true }, s1);
  const live = await store.liveClaims();
  assert.deepEqual(live.filter((c) => c.subject === "port").map((c) => c.status).sort(), ["candidate"], "conflicted subject waits");
  assert.deepEqual(live.filter((c) => c.subject === "lint").map((c) => c.status), ["accepted"]);
});
