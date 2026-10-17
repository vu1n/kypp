import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { D1ClaimCommands, type Group, groupKey } from "../src/claim-commands.ts";
import { type Actor, type Command, type Receipt, Refused, emptyState } from "../src/claim-machine.ts";
import { memoryD1 } from "./d1.ts";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const cases = JSON.parse(execFileSync("python3", ["tests/claim_trace_oracle.py"], { cwd: ROOT, encoding: "utf8" }));
const OWNER: Actor = { principal: "owner", role: "operator" };
const GROUP: Group = { subject: "port", scope: "project", project: "p" };
const correct = (id: string, claim_id = id, revision = 0): Command => ({ id, expected_revision: revision, action: { kind: "Correct", claim_id, content: "answer" } });
const refused = (code: string) => (e: unknown) => e instanceof Refused && e.code === code;

for (const trace of cases) test(`persisted command vector: ${trace.name}`, async () => {
  const commands = new D1ClaimCommands(memoryD1());
  for (const step of trace.steps) {
    if (step.error) await assert.rejects(commands.execute(GROUP, step.actor, step.command, step.stamp ?? undefined), refused(step.error));
    else assert.deepEqual(await commands.execute(GROUP, step.actor, step.command, step.stamp ?? undefined), step.receipt);
    assert.deepEqual(await commands.read(GROUP), step.state);
  }
});

test("a replay after later mutations returns the original receipt and trusted stamp", async () => {
  const db = memoryD1(), commands = new D1ClaimCommands(db);
  const c: Command = { id: "create", expected_revision: 0, action: { kind: "Propose", claim_id: "a", content: "café 日本語" } };
  const first = await commands.execute(GROUP, OWNER, c, { source: "original" });
  await commands.execute(GROUP, OWNER, correct("fix", "b", 1));
  const reopened = new D1ClaimCommands(db);
  assert.deepEqual(await reopened.execute(GROUP, OWNER, c, { source: "new-stamp" }), first);
  const state = await reopened.read(GROUP);
  assert.equal(state.revision, 2);
  assert.equal(state.claims[0].source, "original");
  await assert.rejects(reopened.execute(GROUP, OWNER, { ...c, expected_revision: 2 }), refused("idempotency_conflict"));
});

test("group identity and claim ids cannot be retargeted, even at the same revision", async () => {
  const commands = new D1ClaimCommands(memoryD1()), other: Group = { ...GROUP, project: "q" };
  await commands.execute(GROUP, OWNER, correct("create", "a"));
  await assert.rejects(commands.execute(other, OWNER, correct("create", "a")), refused("idempotency_conflict"));
  await assert.rejects(commands.execute(other, OWNER, correct("another", "a")), refused("id_exists"));
  assert.deepEqual(await commands.read(other), emptyState());
});

test("group keys preserve scope, project and private owner without granting authorization", async () => {
  const groups: Group[] = [
    { subject: "s", scope: "project", project: "p" }, { subject: "s", scope: "project", project: "q" },
    { subject: "s", scope: "group", project: "p" }, { subject: "s", scope: "global" },
    { subject: "s", scope: "user", owner: "a" }, { subject: "s", scope: "user", owner: "b" },
    { subject: "s", scope: "room", owner: "a" }, { subject: "s", scope: "room", owner: "b" },
  ];
  assert.equal(new Set(groups.map(groupKey)).size, groups.length);
  const commands = new D1ClaimCommands(memoryD1());
  for (let i = 0; i < groups.length; i++) await commands.execute(groups[i], OWNER, correct(`c-${i}`));
  for (let i = 0; i < groups.length; i++) assert.equal((await commands.read(groups[i])).head, `c-${i}`);
  for (const bad of [{ subject: "s", scope: "room" }, { subject: "s", scope: "project" }, { subject: "s", scope: "global", project: "p" }]) {
    assert.throws(() => groupKey(bad as Group), refused("invalid_group"));
  }
});

// Interleave after the read/plan, before the actual transaction starts. The batch
// still runs real SQL with rollback; no fake "successful" conditional write.
function interleaved(db: D1Database, hook: () => Promise<unknown>): D1Database {
  let pending = true;
  return { prepare: db.prepare.bind(db), async batch(stmts: D1PreparedStatement[]) {
    if (pending) { pending = false; await hook(); }
    return db.batch(stmts);
  } } as D1Database;
}

test("CAS at the mutation boundary refuses a stale correction without any partial state", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db);
  const commands = new D1ClaimCommands(interleaved(db, () => other.execute(GROUP, OWNER, correct("first"))));
  await assert.rejects(commands.execute(GROUP, OWNER, correct("late")), refused("stale"));
  const state = await commands.read(GROUP);
  assert.equal(state.head, "first");
  assert.deepEqual(state.claims.map((c) => c.id), ["first"]);
  assert.equal(state.events.length, 1);
});

test("duplicate delivery racing the commit produces one event and one receipt", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same");
  let first: Receipt;
  const commands = new D1ClaimCommands(interleaved(db, async () => { first = await other.execute(GROUP, OWNER, c); }));
  assert.deepEqual(await commands.execute(GROUP, OWNER, c), first!);
  const state = await commands.read(GROUP);
  assert.equal(state.revision, 1);
  assert.equal(state.events.length, 1);
  assert.equal(state.receipts.length, 1);
});

test("cross-group command reuse racing the commit rolls back the losing snapshot and id", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), elsewhere: Group = { ...GROUP, project: "q" };
  const commands = new D1ClaimCommands(interleaved(db, () => other.execute(elsewhere, OWNER, correct("same", "b"))));
  await assert.rejects(commands.execute(GROUP, OWNER, correct("same", "a")), refused("idempotency_conflict"));
  assert.deepEqual(await commands.read(GROUP), emptyState());
  assert.equal(await db.prepare("SELECT claim_id FROM claim_command_ids WHERE claim_id='a'").first(), null);
});

test("a failure after saving the snapshot rolls back history, ids and receipt together", async () => {
  const db = memoryD1();
  const broken = { prepare: db.prepare.bind(db), batch(stmts: D1PreparedStatement[]) {
    return db.batch([...stmts.slice(0, -1), db.prepare("INSERT INTO missing_table VALUES(1)"), ...stmts.slice(-1)]);
  } } as D1Database;
  const commands = new D1ClaimCommands(broken);
  await assert.rejects(commands.execute(GROUP, OWNER, correct("create")), /missing_table/);
  assert.deepEqual(await commands.read(GROUP), emptyState());
  for (const table of ["claim_command_ids", "claim_command_receipts", "claim_command_groups"]) {
    assert.equal((await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>())!.n, 0);
  }
  await new D1ClaimCommands(db).execute(GROUP, OWNER, correct("create"));
});

test("the aggregate is isolated from legacy reads and writes until full cutover", async () => {
  const db = memoryD1(), commands = new D1ClaimCommands(db);
  await commands.execute(GROUP, OWNER, correct("v1"));
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM memory_claims").first<{ n: number }>())!.n, 0);
  assert.equal((await commands.read(GROUP)).head, "v1");
});
