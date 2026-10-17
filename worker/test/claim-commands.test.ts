import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { D1ClaimCommands, type Group, groupKey } from "../src/claim-commands.ts";
import { type Actor, type Command, type Receipt, Refused, emptyState, encoded } from "../src/claim-machine.ts";
import { memoryD1 } from "./d1.ts";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const cases = JSON.parse(execFileSync("python3", ["tests/claim_trace_oracle.py"], { cwd: ROOT, encoding: "utf8" }));
const OWNER: Actor = { principal: "owner", role: "operator" };
const GROUP: Group = { subject: "port", scope: "project", project: "p" };
const correct = (id: string, claim_id = id, revision = 0): Command => ({ id, expected_revision: revision, action: { kind: "Correct", claim_id, content: "answer" } });
const refused = (code: string) => (e: unknown) => e instanceof Refused && e.code === code;
const SAME_RECEIPT: Receipt = { principal: "owner", command_id: "same",
  fingerprint: "e390067f0ecee3e8253bb6eca0b17e8bc5e0ad24076915fe98da5b6f0d641694", revision: 1, claim_id: "a", token: null };

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

// Pause after the first lookup returned no receipt, before any state is loaded.
function afterReceiptMiss(db: D1Database, hook: () => Promise<unknown>, afterClaims?: () => Promise<unknown>): D1Database {
  let fired = false;
  return { prepare(sql: string) {
    function wrap(stmt: D1PreparedStatement): D1PreparedStatement {
      return { bind: (...args: unknown[]) => wrap(stmt.bind(...args)), run: stmt.run.bind(stmt),
        async all() {
          const rows = await stmt.all();
          if (afterClaims && sql.startsWith("SELECT claim FROM")) await afterClaims();
          return rows;
        },
        async first() {
          const row = await stmt.first();
          if (!fired && row === null && sql.startsWith("SELECT group_key,fingerprint,receipt")) {
            fired = true;
            await hook();
          }
          return row;
        } } as D1PreparedStatement;
    }
    return wrap(db.prepare(sql));
  }, batch: db.batch.bind(db) } as D1Database;
}

async function assertCommandRows(db: D1Database, count: number) {
  for (const table of ["claim_command_events", "claim_command_receipts"]) {
    assert.equal((await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>())!.n, count);
  }
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM claim_command_ids").first<{ n: number }>())!.n, 1);
  assert.equal((await db.prepare("SELECT revision FROM claim_command_groups").first<{ revision: number }>())!.revision, count);
}

for (const retire of [false, true]) test(`retry after receipt miss and concurrent commit${retire ? " plus retirement" : ""} returns original receipt`, async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same", "a");
  const wrapped = afterReceiptMiss(db, async () => {
    assert.deepEqual(await other.execute(GROUP, OWNER, c), SAME_RECEIPT);
    if (retire) await other.execute(GROUP, OWNER, { id: "retire", expected_revision: 1, action: { kind: "Reject", claim_id: "a" } });
  });
  wrapped.batch = async () => { throw new Error("retry must not write"); };
  const commands = new D1ClaimCommands(wrapped);
  assert.deepEqual(await commands.execute(GROUP, OWNER, c), SAME_RECEIPT);
  await assertCommandRows(db, retire ? 2 : 1);
  const state = await commands.read(GROUP);
  assert.equal(state.claims[0].status, retire ? "rejected" : "accepted");
  assert.equal(state.head, retire ? null : "a");
});

for (const sameCommand of [true, false]) test(`load retry exhaustion ${sameCommand ? "recovers concurrent exact receipt" : "without matching receipt stays stale"}`, async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same", "a");
  const first = sameCommand ? c : { ...c, id: "unrelated" };
  const revisions: number[] = [];
  const wrapped = afterReceiptMiss(db, () => other.execute(GROUP, OWNER, first), async () => {
    assert.ok(revisions.length < 5, "load exceeded its retry bound");
    const revision = revisions.length + 1;
    revisions.push((await other.execute(GROUP, OWNER, correct(`churn-${revision}`, `churn-${revision}`, revision))).revision);
  });
  let writes = 0;
  wrapped.batch = async () => { writes++; throw new Error("retry must not write"); };
  const commands = new D1ClaimCommands(wrapped);
  if (sameCommand) assert.deepEqual(await commands.execute(GROUP, OWNER, c), SAME_RECEIPT);
  else await assert.rejects(commands.execute(GROUP, OWNER, c), refused("stale"));
  assert.equal(writes, 0);
  assert.deepEqual(revisions, [2, 3, 4, 5, 6]);
  for (const table of ["claim_command_ids", "claim_command_events", "claim_command_receipts"]) {
    assert.equal((await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>())!.n, 6);
  }
  assert.equal((await db.prepare("SELECT revision FROM claim_command_groups").first<{ revision: number }>())!.revision, 6);
  const row = await db.prepare("SELECT receipt FROM claim_command_receipts WHERE command_id='same'").first<{ receipt: string }>();
  assert.deepEqual(row ? JSON.parse(row.receipt) : null, sameCommand ? SAME_RECEIPT : null);
});

test("changed payload after receipt miss reports conflict rather than planning stale", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same", "a");
  const commands = new D1ClaimCommands(afterReceiptMiss(db, () => other.execute(GROUP, OWNER, c)));
  await assert.rejects(commands.execute(GROUP, OWNER, { ...c, action: { kind: "Correct", claim_id: "a", content: "different" } }), refused("idempotency_conflict"));
  await assertCommandRows(db, 1);
  assert.equal((await commands.read(GROUP)).claims[0].content, "answer");
});

test("unrelated commit after receipt miss still refuses stale without writes", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db);
  const commands = new D1ClaimCommands(afterReceiptMiss(db, () => other.execute(GROUP, OWNER, correct("first", "a"))));
  await assert.rejects(commands.execute(GROUP, OWNER, correct("late", "b")), refused("stale"));
  await assertCommandRows(db, 1);
  assert.equal(await db.prepare("SELECT receipt FROM claim_command_receipts WHERE command_id='late'").first(), null);
});

test("changed revision after receipt miss reports conflict rather than id_exists", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same", "a");
  const commands = new D1ClaimCommands(afterReceiptMiss(db, () => other.execute(GROUP, OWNER, c)));
  await assert.rejects(commands.execute(GROUP, OWNER, { ...c, expected_revision: 1 }), refused("idempotency_conflict"));
  await assertCommandRows(db, 1);
});

test("read storage failure is not masked by a concurrent receipt", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same", "a");
  const wrapped = afterReceiptMiss(db, () => other.execute(GROUP, OWNER, c));
  const prepare = wrapped.prepare.bind(wrapped);
  wrapped.prepare = (sql: string) => prepare(sql.startsWith("SELECT revision,state") ? "SELECT * FROM missing_table" : sql);
  await assert.rejects(new D1ClaimCommands(wrapped).execute(GROUP, OWNER, c), /missing_table/);
  await assertCommandRows(db, 1);
});

test("storage refusal is not masked by a concurrent receipt", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db), c = correct("same", "a");
  const commands = new D1ClaimCommands(afterReceiptMiss(db, async () => {
    await other.execute(GROUP, OWNER, c);
    await db.prepare("UPDATE claim_command_ids SET claim=NULL WHERE claim_id='a'").run();
  }));
  await assert.rejects(commands.execute(GROUP, OWNER, c), refused("storage_inconsistent"));
  await assertCommandRows(db, 1);
});

test("invalid actor and policy are refused before receipt lookup", async () => {
  const db = memoryD1(), c = correct("same", "a");
  await new D1ClaimCommands(db).execute(GROUP, OWNER, c);
  const noReads = { prepare() { throw new Error("must validate before replay"); }, batch: db.batch.bind(db) } as unknown as D1Database;
  const commands = new D1ClaimCommands(noReads);
  for (const actor of [{ principal: "owner", role: "revoked" } as unknown as Actor, { principal: "", role: "operator" } as Actor]) {
    await assert.rejects(commands.execute(GROUP, actor, c), refused("forbidden"));
  }
  await assert.rejects(commands.execute(GROUP, OWNER, c, undefined, 1), refused("invalid"));
  await assertCommandRows(db, 1);
});

test("role change cannot replay an operator receipt", async () => {
  const db = memoryD1(), commands = new D1ClaimCommands(db), c = correct("same", "a");
  await commands.execute(GROUP, OWNER, c);
  await assert.rejects(commands.execute(GROUP, { principal: "owner", role: "agent" }, c), refused("idempotency_conflict"));
  await assertCommandRows(db, 1);
});

test("caller must reauthorize group reach before replay", async () => {
  // Contract fixture for the future transport boundary, not production auth.
  const db = memoryD1(), commands = new D1ClaimCommands(db), c = correct("same", "a");
  const allowed = new Set([groupKey(GROUP)]);
  const authorizedExecute = async (adapter: D1ClaimCommands, group: Group) => {
    if (!allowed.has(groupKey(group))) throw new Refused("forbidden");
    return adapter.execute(group, OWNER, c);
  };
  assert.deepEqual(await authorizedExecute(commands, GROUP), SAME_RECEIPT);
  allowed.clear();
  const noReads = { prepare() { throw new Error("revoked reach must not read receipts"); }, batch: db.batch.bind(db) } as unknown as D1Database;
  await assert.rejects(authorizedExecute(new D1ClaimCommands(noReads), GROUP), refused("forbidden"));
  // The in-process adapter intentionally trusts its caller's scope decision.
  assert.deepEqual(await commands.execute(GROUP, OWNER, c), SAME_RECEIPT);
  await assertCommandRows(db, 1);
});

test("CAS at the mutation boundary refuses a stale correction without any partial state", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db);
  const commands = new D1ClaimCommands(interleaved(db, () => other.execute(GROUP, OWNER, correct("first"))));
  await assert.rejects(commands.execute(GROUP, OWNER, correct("late")), refused("stale"));
  const state = await commands.read(GROUP);
  assert.equal(state.head, "first");
  assert.deepEqual(state.claims.map((c) => c.id), ["first"]);
  assert.equal(state.events.length, 1);
});

test("multi-query reads retry a version changed between projection and history queries", async () => {
  const db = memoryD1(), other = new D1ClaimCommands(db);
  await other.execute(GROUP, OWNER, correct("first", "a"));
  let fired = false;
  const wrapped = { prepare(sql: string) {
    function wrap(stmt: D1PreparedStatement): D1PreparedStatement {
      return { bind: (...args: unknown[]) => wrap(stmt.bind(...args)), first: stmt.first.bind(stmt), run: stmt.run.bind(stmt),
        async all() {
          const rows = await stmt.all();
          if (!fired && sql.startsWith("SELECT claim FROM")) { fired = true; await other.execute(GROUP, OWNER, correct("second", "b", 1)); }
          return rows;
        } } as D1PreparedStatement;
    }
    return wrap(db.prepare(sql));
  }, batch: db.batch.bind(db) } as D1Database;
  const state = await new D1ClaimCommands(wrapped).read(GROUP);
  assert.equal(fired, true);
  assert.deepEqual(state, await other.read(GROUP));
  assert.equal(state.head, "b");
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

test("the harness enforces UTF-8 value and command row limits", async () => {
  const db = memoryD1();
  await assert.rejects(db.prepare("INSERT INTO claim_command_groups(group_key,revision,state) VALUES(?,?,?)")
    .bind("value", 0, "é".repeat(1_000_001)).run(), /size limit/);
  await assert.rejects(db.prepare("INSERT INTO claim_command_groups(group_key,revision,state) VALUES(?,?,?)")
    .bind("a".repeat(1_100_000), 0, "b".repeat(1_100_000)).run(), /row size limit/);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM claim_command_groups").first<{ n: number }>())!.n, 0);
});

test("4001 commands fit size limits without loading or duplicating history during mutation", async () => {
  const db = memoryD1();
  let permitHistory = false;
  const guarded = { prepare(sql: string) {
    assert.ok(permitHistory || (!sql.startsWith("SELECT event FROM") && !sql.startsWith("SELECT receipt FROM")), sql);
    return db.prepare(sql);
  }, batch: db.batch.bind(db) } as D1Database;
  const commands = new D1ClaimCommands(guarded);
  const create: Command = { id: "create", expected_revision: 0,
    action: { kind: "Propose", claim_id: "a", content: "answer", verifier: "true" } };
  const original = await commands.execute(GROUP, OWNER, create, { source: "original" });
  let revision = 1, firstCheck: { command: Command; receipt: Receipt };
  for (let i = 0; i < 2000; i++) {
    const begin: Command = { id: `begin-${i}`, expected_revision: revision, action: { kind: "BeginCheck", claim_id: "a" } };
    const receipt = await commands.execute(GROUP, OWNER, begin);
    if (i === 0) firstCheck = { command: begin, receipt };
    revision++;
    await commands.execute(GROUP, OWNER, { id: `complete-${i}`, expected_revision: revision,
      action: { kind: "CompleteCheck", token: receipt.token!, outcome: "passed" } });
    revision++;
  }
  const reopened = new D1ClaimCommands(guarded);
  assert.deepEqual(await reopened.execute(GROUP, OWNER, create, { source: "new-stamp" }), original);
  assert.deepEqual(await reopened.execute(GROUP, OWNER, firstCheck!.command), firstCheck!.receipt);
  await assert.rejects(reopened.execute({ ...GROUP, project: "q" }, OWNER, create), refused("idempotency_conflict"));
  await assert.rejects(reopened.execute(GROUP, OWNER, { ...create, expected_revision: revision }), refused("idempotency_conflict"));
  permitHistory = true;
  const state = await reopened.read(GROUP);
  assert.equal(state.revision, 4001);
  assert.equal(state.events.length, 4001);
  assert.equal(state.receipts.length, 4001);
  assert.ok(Buffer.byteLength(encoded(state)) > 2_000_000, "old aggregate would exceed D1");
  const metadata = (await db.prepare("SELECT state FROM claim_command_groups").first<{ state: string }>())!.state;
  assert.ok(Buffer.byteLength(metadata) < 200);
  assert.equal("receipts" in JSON.parse(metadata), false);
  assert.equal("events" in JSON.parse(metadata), false);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM claim_command_receipts").first<{ n: number }>())!.n, 4001);
});

test("500 large claims remain separate rows, including after atomic correction", async () => {
  const db = memoryD1(), commands = new D1ClaimCommands(db);
  for (let i = 0; i < 500; i++) await commands.execute(GROUP, OWNER, { id: `c-${i}`, expected_revision: i,
    action: { kind: "Propose", claim_id: `a-${i}`, content: "x".repeat(4000) } }, { source: `s-${i}` });
  assert.equal((await commands.read(GROUP)).claims.length, 500);
  assert.ok((await db.prepare("SELECT SUM(length(CAST(claim AS BLOB))) AS n FROM claim_command_ids").first<{ n: number }>())!.n > 2_000_000);
  assert.ok((await db.prepare("SELECT MAX(length(CAST(claim AS BLOB))) AS n FROM claim_command_ids").first<{ n: number }>())!.n < 5000);
  await commands.execute(GROUP, OWNER, correct("fix", "human", 500));
  const state = await commands.read(GROUP);
  assert.equal(state.head, "human");
  assert.equal(state.claims.length, 501);
  assert.ok(state.claims.slice(0, -1).every((c) => c.status === "superseded" && c.successor === "human"));
});

for (const table of ["claim_command_events", "claim_command_receipts"]) {
  test(`a real ${table} insertion failure rolls back the changed head and projections`, async () => {
    const db = memoryD1(), commands = new D1ClaimCommands(db);
    await commands.execute(GROUP, OWNER, correct("create", "a"));
    const before = await commands.read(GROUP);
    await db.prepare(`CREATE TRIGGER fail_insert BEFORE INSERT ON ${table} BEGIN SELECT RAISE(ABORT,'injected failure'); END`).run();
    await assert.rejects(commands.execute(GROUP, OWNER, correct("fix", "b", 1)), /injected failure/);
    assert.deepEqual(await commands.read(GROUP), before);
    assert.equal(await db.prepare("SELECT claim_id FROM claim_command_ids WHERE claim_id='b'").first(), null);
    await db.prepare("DROP TRIGGER fail_insert").run();
    await commands.execute(GROUP, OWNER, correct("fix", "b", 1));
  });
}

async function snapshotFixture(name = "scheduled check recovery") {
  const db = memoryD1({ throughMigration: 10 });
  const trace = cases.find((c: any) => c.name.includes(name));
  const state = trace.steps.at(-1).state;
  await db.batch([
    db.prepare("INSERT INTO claim_command_groups(group_key,revision,state) VALUES(?,?,?)").bind(groupKey(GROUP), state.revision, encoded(state)),
    ...state.claims.map((c: any) => db.prepare("INSERT INTO claim_command_ids(claim_id,group_key) VALUES(?,?)").bind(c.id, groupKey(GROUP))),
    ...state.receipts.map((r: Receipt) => db.prepare("INSERT INTO claim_command_receipts(principal,command_id,group_key,fingerprint,receipt) VALUES(?,?,?,?,?)")
      .bind(r.principal, r.command_id, groupKey(GROUP), r.fingerprint, encoded(r))),
  ]);
  return { db, trace, state };
}

test("the old staged snapshot converts losslessly with old replay tokens unchanged", async () => {
  const { db, trace, state } = await snapshotFixture();
  db.applyRemainingMigrations();
  db.applyRemainingMigrations();
  const commands = new D1ClaimCommands(db);
  assert.deepEqual(await commands.read(GROUP), state);
  for (const step of trace.steps) assert.deepEqual(await commands.execute(GROUP, step.actor, step.command, step.stamp ?? undefined), step.receipt);
  const receipt = await commands.execute(GROUP, OWNER, { id: "new-check", expected_revision: state.revision, action: { kind: "BeginCheck", claim_id: "a" } });
  assert.equal(receipt.token!.attempt, 4);
});

test("a failed staged conversion preserves the old snapshot and can retry", async () => {
  const { db, state } = await snapshotFixture();
  await db.prepare("CREATE TRIGGER fail_conversion BEFORE UPDATE ON claim_command_groups BEGIN SELECT RAISE(ABORT,'conversion failure'); END").run();
  assert.throws(() => db.applyRemainingMigrations(), /conversion failure/);
  assert.equal((await db.prepare("SELECT state FROM claim_command_groups").first<{ state: string }>())!.state, encoded(state));
  assert.equal(await db.prepare("SELECT name FROM sqlite_master WHERE name='claim_command_events'").first(), null);
  await db.prepare("DROP TRIGGER fail_conversion").run();
  db.applyRemainingMigrations();
  assert.deepEqual(await new D1ClaimCommands(db).read(GROUP), state);
});

test("conversion retains recurrence evidence and retired original-source history", async () => {
  const { db, state } = await snapshotFixture("new recurrence observation");
  db.applyRemainingMigrations();
  assert.deepEqual(await new D1ClaimCommands(db).read(GROUP), state);
  assert.ok(state.events.at(-1).evidence.length);
  assert.ok(state.claims.some((c: any) => c.status === "superseded" && c.source));
});
