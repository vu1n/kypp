import assert from "node:assert/strict";
import { test } from "node:test";
import { handleMcp, issueSession, verifySession } from "../src/mcp.ts";
import { memoryD1 } from "./d1.ts";

test("session ids are signed; forged or unsigned ids give no session", async () => {
  const issued = await issueSession("k");
  const [id] = issued.split(".");
  assert.equal(await verifySession("k", issued), id);
  assert.equal(await verifySession("k", `${id}.0000000000000000`), null);
  assert.equal(await verifySession("k", "made-up"), null);
  assert.equal(await verifySession("other", issued), null);
  assert.equal(await verifySession("k", null), null);
});

test("with correct off, the tool is hidden from the list and refused when called", async () => {
  const db = memoryD1();
  const caller = { user: "owner", agent: "t" };
  const rpc = (method: string, params?: unknown) => new Request("https://k/mcp", {
    method: "POST", headers: { "X-Kypp-Project": "p" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const call = async (method: string, params?: unknown, correct?: boolean) =>
    (await (await handleMcp(rpc(method, params), db, caller, { correct })).json()).result;
  const names = (r: any) => r.tools.map((t: any) => t.name);
  assert.ok(names(await call("tools/list")).includes("correct"));
  assert.ok(!names(await call("tools/list", undefined, false)).includes("correct"));
  assert.ok(!(await call("initialize", {}, false)).instructions.includes("correct("));
  const refused = await call("tools/call", { name: "correct", arguments: { subject: "s", content: "c" } }, false);
  assert.equal(refused.isError, true);
});
