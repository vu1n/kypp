import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { test } from "node:test";
import { handleMcp } from "../src/mcp.ts";
import { memoryD1 } from "./d1.ts";

const sha = execSync("git rev-parse HEAD", { cwd: new URL("../..", import.meta.url) }).toString().trim();
const evidence = `sha ${sha}; node ${process.version}`;

type Row = { authority?: string; verify?: string | null; content?: string; [key: string]: unknown };

function assertBoundary(stored: Row[]) {
  for (const row of stored) {
    // Null means the column is absent of a verifier. "" is still a stored verifier.
    if (Object.hasOwn(row, "verify") && row.verify != null) {
      throw new Error(`verifier persisted: ${JSON.stringify(row.verify)}`);
    }
    if (row.authority === "verified" || row.authority === "human") {
      throw new Error(`privileged authority write: ${row.authority}`);
    }
  }
}

async function rows(db: D1Database): Promise<Row[]> {
  const info = await db.prepare("PRAGMA table_info(memory_claims)").all();
  const names = (info.results as { name: string }[]).map((col) => col.name);
  const out = await db.prepare("SELECT * FROM memory_claims").all();
  const stored = out.results as Row[];
  for (const row of stored) {
    for (const name of names) {
      if (!Object.hasOwn(row, name)) throw new Error(`collector dropped column ${name}`);
    }
  }
  return stored;
}

// Models a future regression: tools/call's verify argument is written onto the row.
// The production schema has no verify column; this wrapper adds one only in the test.
function forwardingDb(base: D1Database, forwarded: { value: string | null }): D1Database {
  return {
    prepare(sql: string) {
      const stmt = base.prepare(sql);
      const wrap = (bound: { run: () => Promise<unknown>; all: () => Promise<unknown>; first: () => Promise<unknown> }, args: unknown[]) => ({
        async run() {
          const result = await bound.run();
          if (forwarded.value != null && /insert\s+into\s+memory_claims\b/i.test(sql)) {
            await base.prepare("UPDATE memory_claims SET verify = ? WHERE id = ?").bind(forwarded.value, args[0]).run();
          }
          return result;
        },
        all: () => bound.all(),
        first: () => bound.first(),
      });
      return {
        bind(...args: unknown[]) { return wrap(stmt.bind(...args) as { run: () => Promise<unknown>; all: () => Promise<unknown>; first: () => Promise<unknown> }, args); },
        all: () => stmt.all(),
        first: () => stmt.first(),
        run: () => stmt.run(),
      };
    },
    batch: (stmts: unknown[]) => base.batch(stmts as never),
  } as unknown as D1Database;
}

function rpc(method: string, params?: unknown) {
  return new Request("https://k/mcp", {
    method: "POST",
    headers: { "X-Kypp-Project": "pillbox" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
}

async function call(db: D1Database, method: string, params?: unknown) {
  const body = await (await handleMcp(rpc(method, params), db, { user: "owner", agent: "contract" })).json() as {
    result?: { tools?: { name: string; inputSchema?: { properties?: Record<string, unknown> } }[]; content?: { text?: string }[]; isError?: boolean };
  };
  return body.result;
}

test("evidence names the candidate sha and the runtime", () => {
  assert.match(evidence, /sha [0-9a-f]{40}/);
  assert.match(evidence, /node v/);
  console.log(evidence);
});

test("declared tools have no verifier parameter and every tool is called", async () => {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)")
    .bind("pillbox", "", "2026-10-13T00:00:00Z").run();
  const listed = await call(db, "tools/list");
  const tools = listed?.tools ?? [];
  assert.ok(tools.length > 0);
  const names = tools.map((tool) => tool.name);
  assert.deepEqual(names, ["briefing", "recall", "claim", "expand", "correct"]);
  for (const tool of tools) {
    const props = tool.inputSchema?.properties ?? {};
    assert.equal(Object.hasOwn(props, "verify"), false, tool.name);
    assert.equal(Object.hasOwn(props, "authority"), false, tool.name);
  }

  const positive: Record<string, Record<string, unknown>> = {
    briefing: {},
    recall: { query: "verify" },
    claim: { subject: "verify wording", content: "please verify this lesson" },
    correct: { subject: "verify wording", content: "the right lesson" },
  };
  const claim = await call(db, "tools/call", { name: "claim", arguments: positive.claim });
  assert.equal(claim?.isError, undefined);
  const claimId = claim?.content?.[0]?.text ?? "";
  assert.match(claimId, /^[0-9a-f]{32}/);
  positive.expand = { handle: claimId.slice(0, 8) };
  const called = ["claim"];
  for (const name of names) {
    if (name === "claim") continue;
    const result = await call(db, "tools/call", { name, arguments: positive[name] });
    assert.notEqual(result?.isError, true, name);
    assert.ok(result?.content?.[0]?.text !== undefined, name);
    called.push(name);
  }
  assert.equal(called.length, names.length);
  const stored = await rows(db);
  assert.ok(stored.some((row) => (row.content ?? "").includes("please verify")));
  assertBoundary(stored);

  for (const name of names) {
    await call(db, "tools/call", {
      name,
      arguments: { ...positive[name], verify: "true", authority: name === "claim" ? "verified" : "human" },
    });
  }
  assertBoundary(await rows(db));
  const after = await rows(db);
  assert.ok(after.every((row) => row.authority === "agent"));
});

test("a verifier forwarded through tools/call fails the ordinary collector", async () => {
  const db = memoryD1();
  await db.prepare("ALTER TABLE memory_claims ADD COLUMN verify TEXT").run();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)")
    .bind("pillbox", "", "2026-10-13T00:00:00Z").run();
  const forwarded = { value: "true" as string | null };
  const wrapped = forwardingDb(db, forwarded);
  await call(wrapped, "tools/call", {
    name: "claim",
    arguments: { subject: "verify wording", content: "please verify this lesson", verify: "true" },
  });
  const persisted = await rows(wrapped);
  assert.equal(persisted.some((row) => row.verify === "true"), true);
  assert.throws(() => assertBoundary(persisted), /verifier persisted/);

  forwarded.value = "";
  await call(wrapped, "tools/call", {
    name: "claim",
    arguments: { subject: "empty verifier", content: "an empty verifier is still stored", verify: "" },
  });
  const emptied = await rows(wrapped);
  assert.equal(emptied.some((row) => row.verify === ""), true);
  assert.throws(() => assertBoundary(emptied), /verifier persisted/);
});

test("a null verifier column is absence, and privileged authority still fails the assertion", async () => {
  const db = memoryD1();
  await db.prepare("ALTER TABLE memory_claims ADD COLUMN verify TEXT").run();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES(?,?,?)")
    .bind("pillbox", "", "2026-10-13T00:00:00Z").run();
  await call(db, "tools/call", {
    name: "claim",
    arguments: { subject: "verify wording", content: "please verify this lesson" },
  });
  const stored = await rows(db);
  assert.ok(stored.length > 0);
  assert.ok(stored.every((row) => Object.hasOwn(row, "verify") && row.verify == null));
  assert.doesNotThrow(() => assertBoundary(stored));
  assert.throws(() => assertBoundary([{ authority: "verified", content: "x" }]));
  assert.throws(() => assertBoundary([{ authority: "human", content: "x" }]));
});
