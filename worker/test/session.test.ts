import assert from "node:assert/strict";
import { test } from "node:test";
import { issueSession, verifySession } from "../src/mcp.ts";

test("session ids are signed; forged or unsigned ids give no session", async () => {
  const issued = await issueSession("k");
  const [id] = issued.split(".");
  assert.equal(await verifySession("k", issued), id);
  assert.equal(await verifySession("k", `${id}.0000000000000000`), null);
  assert.equal(await verifySession("k", "made-up"), null);
  assert.equal(await verifySession("other", issued), null);
  assert.equal(await verifySession("k", null), null);
});
