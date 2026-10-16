import assert from "node:assert/strict";
import { timingSafeEqual } from "node:crypto";
import { test } from "node:test";
import { handleOperator } from "../src/operator.ts";
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

test("a superseded human claim is not re-checked: a newer correction replaced it", async () => {
  const { call } = await setup();
  const old = (await call({ op: "correct", subject: "s", content: "old answer", project: "p", verify: "true" })).body!.id;
  await call({ op: "correct", subject: "s", content: "new answer", project: "p" });
  const listed = (await call({ op: "verifiable", project: "p" })).body!.claims.map((c: any) => c.id);
  assert.ok(!listed.includes(old), "superseded human claim listed for verify");
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
