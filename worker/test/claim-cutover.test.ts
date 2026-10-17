// Hand-authored portable expectations and synthetic legacy-boundary characterization.
// No migration implementation, deployment, production oracle, or live D1 service.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { groupKey, type Group } from "../src/claim-commands.ts";
import { Refused } from "../src/claim-machine.ts";
import { rank, type Claim } from "../src/memory.ts";
import { D1Store } from "../src/store.ts";
import { memoryD1 } from "./d1.ts";

const vectors = JSON.parse(readFileSync(new URL("../../specs/claim_state_machine/cutover-v1.json", import.meta.url), "utf8"));
const invalidGroup = (error: unknown) => error instanceof Refused && error.code === "invalid_group";

test("hand-authored canonical keys and legacy rank cells are portable", () => {
  const keys = vectors.groups.map(({ canonical }: { canonical: [string, Group["scope"], string | null, string | null] }) => {
    const [subject, scope, project, owner] = canonical;
    const key = groupKey({ subject, scope, project, owner });
    assert.deepEqual(JSON.parse(key), canonical);
    return key;
  });
  assert.equal(keys[0], keys[1]);
  assert.equal(new Set(keys.slice(2)).size, 6);
  for (const cell of vectors.rank) assert.deepEqual(rank(cell as Claim), cell.expected);
  assert.throws(() => groupKey(vectors.unplaced), invalidGroup);
  for (const row of vectors.unsupported) assert.throws(() => groupKey({
    subject: row.subject, scope: row.scope, project: row.project, owner: row.user,
  }), invalidGroup);
});

test("synthetic filing preserves raw identity/metadata and changes only placement, once", async () => {
  const db = memoryD1(), store = new D1Store(db);
  const id = await store.claim({ type: "pitfall", subject: "port", content: "a durable lesson",
    scope: "project", project: null, origin: "unregistered", session: "session-s", user: "alice",
    agent: "model", confidence: 0.7, sourceIds: ["original-source"], accept: false,
    metadata: { s1: { keep: 0.9 }, unknown_future_field: true },
    codeRefs: [{ path: "src/a.ts", symbol: "run" }],
  });
  const syntheticRef = (namespace: string, anchor: string) => `doc://${namespace}/features@0001#${anchor}`;
  await store.linkPointer(syntheticRef("source-doc", "x"), "src/a.ts", id);
  await store.linkPointer(syntheticRef("other-doc", "y"), "src/b.ts");
  const before = await db.prepare("SELECT * FROM memory_claims WHERE id = ?").bind(id).first();
  const pointers = await db.prepare("SELECT * FROM code_pointers ORDER BY id").all();
  const fts = await db.prepare("SELECT * FROM claims_fts").all();
  assert.ok(await store.get(id, "alice", "session-s"));
  assert.equal(await store.get(id, "bob", "session-s"), null);
  assert.equal(await store.get(id, "alice", "another-session"), null);
  assert.deepEqual(await store.liveClaims({ filed: true }), []);
  assert.throws(() => groupKey({ subject: "port", scope: "project", project: null }), invalidGroup);
  assert.equal(await store.file(id, "repo-a", 0.9, "synthetic fixture"), true);
  assert.equal(await store.file(id, "repo-b", 0.9, "duplicate attempt"), false);
  assert.deepEqual({ ...await db.prepare("SELECT * FROM memory_claims WHERE id = ?").bind(id).first() },
    { ...before, project: "repo-a" });
  assert.deepEqual(await db.prepare("SELECT * FROM code_pointers ORDER BY id").all(), pointers);
  assert.deepEqual(await db.prepare("SELECT * FROM claims_fts").all(), fts);
  assert.deepEqual((await db.prepare("SELECT action, from_project, to_project FROM defrag_log").all()).results.map((r) => ({ ...r })),
    [{ action: "file", from_project: null, to_project: "repo-a" }]);
  assert.equal((await store.liveClaims({ filed: true }))[0].id, id);
  // Filing has NOT activated the staged machine or provided its dual revision fence.
  assert.deepEqual((await db.prepare("SELECT * FROM claim_command_groups").all()).results, []);
});
