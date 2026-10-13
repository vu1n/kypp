import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { test } from "node:test";
import { handleMcp } from "../src/mcp.ts";
import { memoryD1 } from "./d1.ts";

const sha = execSync("git rev-parse HEAD", { cwd: new URL("../..", import.meta.url) }).toString().trim();
const evidence = `sha ${sha}; node ${process.version}`;

type Row = { authority?: string; verify?: string; content?: string };

function assertBoundary(rows: Row[]) {
  for (const row of rows) {
    if (row.verify) throw new Error(`verifier persisted: ${row.verify}`);
    if (row.authority === "verified" || row.authority === "human") {
      throw new Error(`privileged authority write: ${row.authority}`);
    }
  }
}

async function rows(db: D1Database): Promise<Row[]> {
  const out = await db.prepare("SELECT authority, content FROM memory_claims").all();
  return out.results as Row[];
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

test("a forwarded verifier fails the boundary assertion", () => {
  assert.throws(() => assertBoundary([{ authority: "agent", verify: "true", content: "please verify" }]));
  assert.throws(() => assertBoundary([{ authority: "verified", content: "x" }]));
  assert.throws(() => assertBoundary([{ authority: "human", content: "x" }]));
});
