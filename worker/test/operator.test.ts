import assert from "node:assert/strict";
import { timingSafeEqual } from "node:crypto";
import { test } from "node:test";
import { handleOperator } from "../src/operator.ts";
import { planConsolidation } from "../src/memory.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const SECRET = "operator-secret-0123456789";
// Workers' crypto.subtle has timingSafeEqual; node's doesn't, so the test borrows node:crypto's.
(crypto.subtle as any).timingSafeEqual ??= (a: ArrayBuffer, b: ArrayBuffer) => timingSafeEqual(new Uint8Array(a), new Uint8Array(b));

async function setup() {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind("p", "", "2026-10-15T00:00:00Z").run();
  const call = async (body: unknown, token: string | null = SECRET, secret: string | null = SECRET) => {
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const r = await handleOperator(new Request("https://k/v1/operator", { method: "POST", headers, body: JSON.stringify(body) }), db, secret ?? undefined);
    return { status: r.status, body: r.status === 404 ? null : await r.json() as Record<string, any> };
  };
  const row = async (id: string) => db.prepare("SELECT * FROM memory_claims WHERE id = ?").bind(id).first() as Promise<Record<string, any>>;
  const logged = async (id: string) => (await db.prepare("SELECT action FROM defrag_log WHERE claim_id = ? ORDER BY created_at").bind(id).all()).results.map((r) => r.action);
  return { db, call, row, logged };
}

test("the route is 404 when no operator token is configured, and 401 for a missing or wrong token", async () => {
  const { call } = await setup();
  assert.equal((await call({ op: "recall", query: "x" }, SECRET, null)).status, 404);
  assert.equal((await call({ op: "recall", query: "x" }, null)).status, 401);
  assert.equal((await call({ op: "recall", query: "x" }, "not-the-secret")).status, 401);
});

test("correct writes human authority, supersedes the subject's agent claim, and is logged", async () => {
  const { call, row, logged } = await setup();
  const agent = (await call({ op: "remember", subject: "deploy", content: "ship any time", project: "p", accept: true })).body!;
  assert.equal((await row(agent.id)).authority, "agent");
  const res = await call({ op: "correct", subject: "deploy", content: "ship on green only", project: "p" });
  assert.equal(res.status, 200);
  assert.equal(res.body!.superseded, 1);
  const human = await row(res.body!.id);
  assert.equal(human.authority, "human");
  assert.equal(human.status, "accepted");
  assert.equal(human.user, "owner");
  assert.equal((await row(agent.id)).status, "superseded");
  assert.deepEqual(await logged(res.body!.id), ["operator:correct"]);
  assert.deepEqual(await logged(agent.id), ["operator:remember", "operator:supersede"]);
});

test("verify lists a project's verifier claims and records each verdict, logged", async () => {
  const { call, row, logged } = await setup();
  const ok = (await call({ op: "remember", subject: "tag", content: "use l7", project: "p", verify: "true" })).body!.id;
  const bad = (await call({ op: "remember", subject: "flag", content: "use --old", project: "p", verify: "false" })).body!.id;
  await call({ op: "remember", subject: "plain", content: "no check", project: "p" });
  const listed = (await call({ op: "verifiable", project: "p" })).body!.claims;
  assert.deepEqual(listed.map((c: any) => c.verify).sort(), ["false", "true"]);
  await call({ op: "mark_verified", id: ok, passed: true });
  await call({ op: "mark_verified", id: bad, passed: false });
  assert.equal((await row(ok)).authority, "verified");
  assert.equal((await row(ok)).status, "accepted");
  assert.equal((await row(bad)).status, "rejected");
  assert.deepEqual(await logged(ok), ["operator:remember", "operator:verify"]);
  // A claim without a verifier can't be marked verified.
  const plain = (await call({ op: "remember", subject: "x", content: "y", project: "p" })).body!.id;
  assert.equal((await call({ op: "mark_verified", id: plain, passed: true })).status, 400);
  assert.equal((await row(plain)).authority, "agent");
});

test("a passing check keeps a human claim human; a failing one rejects it", async () => {
  const { call, row } = await setup();
  const id = (await call({ op: "correct", subject: "s", content: "c", project: "p", verify: "true" })).body!.id;
  await call({ op: "mark_verified", id, passed: true });
  assert.equal((await row(id)).authority, "human");
  await call({ op: "mark_verified", id, passed: false });
  assert.equal((await row(id)).status, "rejected");
});

test("a superseded human claim is not re-checked: a same-millisecond correction replaced it", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-18T00:00:00Z") });
  const { call, row } = await setup();
  const old = (await call({ op: "correct", subject: "s", content: "old answer", project: "p", verify: "true" })).body!.id;
  const fixed = await call({ op: "correct", subject: "s", content: "new answer", project: "p" });
  assert.equal(fixed.status, 200);
  assert.equal(fixed.body!.superseded, 1);
  assert.equal((await row(old)).status, "superseded");
  assert.equal((await row(fixed.body!.id)).status, "accepted");
  const listed = (await call({ op: "verifiable", project: "p" })).body!.claims.map((c: any) => c.id);
  assert.ok(!listed.includes(old), "superseded human claim listed for verify");
});

for (const reverse of [false, true]) {
  test(`correction replaces the exact group regardless of rank or read order (reverse=${reverse})`, async (t) => {
    t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-18T00:00:00Z") });
    const { db, call, row, logged } = await setup();
    await db.prepare(`PRAGMA reverse_unordered_selects=${reverse ? "ON" : "OFF"}`).run();
    await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)").bind("q", "", "2026-10-18T00:00:00Z").run();
    const old = (await call({ op: "correct", subject: "s", content: "old", project: "p", confidence: 1 })).body!.id;
    const pending = (await call({ op: "remember", subject: "s", content: "pending", project: "p" })).body!.id;
    const untouched = [];
    for (const args of [{ subject: "other", project: "p" }, { subject: "s", project: "q" }, { subject: "s", scope: "global" }, { subject: "s", scope: "user" }]) {
      untouched.push((await call({ op: "remember", content: "unrelated", ...args })).body!.id);
    }
    const rejected = (await call({ op: "remember", subject: "s", content: "rejected", project: "p" })).body!.id;
    await call({ op: "reject", handle: rejected });
    const fixed = await call({ op: "correct", subject: "s", content: "replacement", project: "p", confidence: 0 });
    assert.equal(fixed.status, 200);
    assert.equal(fixed.body!.superseded, 2);
    assert.equal((await row(fixed.body!.id)).status, "accepted");
    assert.equal((await row(fixed.body!.id)).authority, "human");
    for (const id of [old, pending]) {
      assert.equal((await row(id)).status, "superseded");
      assert.equal((await logged(id)).filter((action) => action === "operator:supersede").length, 1);
      assert.equal((await db.prepare("SELECT reason FROM defrag_log WHERE claim_id=? AND action='operator:supersede'").bind(id).first())!.reason, `by ${fixed.body!.id}`);
    }
    for (const id of untouched) assert.equal((await row(id)).status, "candidate");
    assert.equal((await row(rejected)).status, "rejected");
    assert.deepEqual(await logged(rejected), ["operator:remember", "operator:reject"]);
  });
}

test("user corrections leave another owner's private claims alone", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-18T00:00:00Z") });
  const { db, call, row } = await setup();
  const other = (await call({ op: "remember", subject: "s", content: "private", scope: "user" })).body!.id;
  await db.prepare("UPDATE memory_claims SET user='other' WHERE id=?").bind(other).run();
  const own = (await call({ op: "remember", subject: "s", content: "mine", scope: "user" })).body!.id;
  const fixed = await call({ op: "correct", subject: "s", content: "new", scope: "user" });
  assert.equal(fixed.body!.superseded, 1);
  assert.equal((await row(own)).status, "superseded");
  assert.equal((await row(other)).status, "candidate");
});

test("global correction replaces global claims across writers without touching private scope", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-18T00:00:00Z") });
  const { db, call, row } = await setup();
  const old = (await call({ op: "correct", subject: "s", content: "old", scope: "global" })).body!.id;
  await db.prepare("UPDATE memory_claims SET user='other' WHERE id=?").bind(old).run();
  const privateId = (await call({ op: "remember", subject: "s", content: "private", scope: "user" })).body!.id;
  const fixed = await call({ op: "correct", subject: "s", content: "new", scope: "global" });
  assert.equal(fixed.body!.superseded, 1);
  assert.equal((await row(fixed.body!.id)).status, "accepted");
  assert.equal((await row(old)).status, "superseded");
  assert.equal((await row(privateId)).status, "candidate");
});

for (const failure of ["supersede-audit", "status-update"]) {
  test(`correction rolls back its claim, FTS, pointers and audit on ${failure} failure`, async () => {
    const { db, call, row, logged } = await setup();
    const old = (await call({ op: "correct", subject: "s", content: "old", project: "p" })).body!.id;
    const trigger = failure === "supersede-audit"
      ? "BEFORE INSERT ON defrag_log WHEN NEW.action='operator:supersede'"
      : "BEFORE UPDATE ON memory_claims WHEN NEW.status='superseded'";
    await db.prepare(`CREATE TRIGGER fail_correction ${trigger} BEGIN SELECT RAISE(ABORT,'injected correction failure'); END`).run();
    const ref = ["doc:", "", "fixture", "contract@0001#rule"].join("/");
    const fixed = await call({ op: "correct", subject: "s", content: `replacement ${ref}`, project: "p" });
    assert.equal(fixed.status, 400);
    assert.match(fixed.body!.error, /injected correction failure/);
    assert.equal((await row(old)).status, "accepted");
    assert.deepEqual(await logged(old), ["operator:correct"]);
    for (const table of ["memory_claims", "claims_fts", "defrag_log", "code_pointers"]) {
      const expected = table === "code_pointers" ? 0 : 1;
      assert.equal((await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first())!.n, expected, table);
    }
  });
}

test("overlapping corrections follow transaction order even when timestamps run backwards", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-18T00:00:00Z") });
  const { db, call, row } = await setup();
  const batch = db.batch.bind(db);
  let inner: Record<string, any> | null = null;
  let entered = false;
  db.batch = async (statements) => {
    if (!entered) {
      entered = true;
      t.mock.timers.setTime(Date.now() + 1000);
      inner = (await call({ op: "correct", subject: "s", content: "committed first", project: "p" })).body!;
    }
    return batch(statements);
  };
  const outer = await call({ op: "correct", subject: "s", content: "committed last", project: "p" });
  assert.equal(outer.status, 200);
  assert.equal(outer.body!.superseded, 1);
  assert.equal((await row(outer.body!.id)).status, "accepted");
  assert.equal((await row(inner!.id)).status, "superseded");
  assert.ok((await row(outer.body!.id)).created_at < (await row(inner!.id)).created_at);
});

test("a same-count same-timestamp stale plan cannot revive claims retired by a correction", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-18T00:00:00Z") });
  const { db, call, row } = await setup();
  const store = new D1Store(db);
  const before = [];
  for (const session of ["A", "B"]) {
    const id = (await call({ op: "remember", subject: "s", content: "old", project: "p" })).body!.id;
    await db.prepare("UPDATE memory_claims SET source_ids=? WHERE id=?").bind(JSON.stringify([session]), id).run();
    before.push(id);
  }
  const stale = planConsolidation(await store.liveClaims());
  assert.equal(stale[0].promote.length, 1);
  const fixed = (await call({ op: "correct", subject: "s", content: "human", project: "p" })).body!.id;
  const pending = (await call({ op: "remember", subject: "s", content: "new proposal", project: "p" })).body!.id;
  assert.deepEqual(await store.apply(stale), { applied: 0, stale: 1 });
  assert.equal((await row(fixed)).status, "accepted");
  assert.equal((await row(pending)).status, "candidate");
  for (const id of before) assert.equal((await row(id)).status, "superseded");
});

test("project writes need a registered project; unknown ops write nothing", async () => {
  const { db, call } = await setup();
  assert.equal((await call({ op: "correct", subject: "s", content: "c", project: "nowhere" })).status, 400);
  assert.equal((await call({ op: "drop_table" })).status, 400);
  // Rooms belong to their members' bots; the operator writes no room lane.
  assert.equal((await call({ op: "remember", subject: "s", content: "c", scope: "room" })).status, 400);
  const n = await db.prepare("SELECT COUNT(*) AS n FROM memory_claims").first() as { n: number };
  assert.equal(n.n, 0);
});
