// Recall's System One rerank (memory-scope-decay §8): keyword search pulls a wide pool, the model
// scores each pooled claim for relevance, claims under the floor drop, and the model can never add a
// claim or widen what a caller sees. Any failure keeps the keyword order.
import assert from "node:assert/strict";
import { test } from "node:test";
import { handleMcp } from "../src/mcp.ts";
import { RELEVANT_FLOOR, RELEVANT_FLOOR_MODEL, RERANK_IN_FLIGHT, RERANK_POOL, RERANK_QUERY, type S1Client, rankForQuery, relevance, relevantFloorFor, rerank, s1Client } from "../src/s1.ts";
import { memoryD1 } from "./d1.ts";

const claim = (subject: string) => ({ subject, content: `${subject} content` });

// Scores each claim by a table on its subject; records every request.
function scorer(table: Record<string, number>, floor = RELEVANT_FLOOR) {
  const calls: any[] = [];
  const client: S1Client = {
    relevantFloor: floor,
    systemOne: async (req: any) => {
      calls.push(req);
      return { answers: Object.fromEntries(req.state.claims.map((c: any) => [`c${c.n}`, { type: "noul", noul: table[c.subject] ?? 0 }])) };
    },
  };
  return { client, calls };
}

test("each pooled claim is scored in its own request, in order, with the query", async () => {
  const { client, calls } = scorer({ a: 0.9, b: 0.1, c: 0.5 });
  assert.deepEqual(await relevance(client, "q", [claim("a"), claim("b"), claim("c")]), [0.9, 0.1, 0.5]);
  assert.equal(calls.length, 3);
  assert.deepEqual(calls.map((r) => [r.state.query, r.state.claims.length, Object.keys(r.questions)]), [["q", 1, ["c0"]], ["q", 1, ["c0"]], ["q", 1, ["c0"]]]);
  // a larger chunk shares one request; the answer is still per claim
  const two = scorer({ a: 0.9, b: 0.1 });
  assert.deepEqual(await relevance(two.client, "q", [claim("a"), claim("b")], 1000, 2), [0.9, 0.1]);
  assert.equal(two.calls.length, 1);
});

test("no model, no query, an error, a timeout or a missing answer is no opinion", async () => {
  assert.equal(await relevance(null, "q", [claim("a")]), null);
  assert.equal(await relevance(scorer({ a: 1 }).client, "  ", [claim("a")]), null);
  assert.equal(await relevance({ systemOne: async () => { throw new Error("down"); } }, "q", [claim("a")]), null);
  assert.equal(await relevance({ systemOne: () => new Promise(() => {}) }, "q", [claim("a")], 20), null);
  assert.equal(await relevance({ systemOne: async () => ({ answers: {} }) }, "q", [claim("a")]), null);
  assert.equal(await relevance({ systemOne: async () => ({ answers: { c0: { type: "noul", noul: 2 } } }) }, "q", [claim("a")]), null);
});

test("the rerank bounds its burst, the query it forwards and the claims it scores", async () => {
  let open = 0, peak = 0;
  const calls: any[] = [];
  const slow: S1Client = { systemOne: async (req: any) => {
    calls.push(req);
    peak = Math.max(peak, ++open);
    await new Promise((r) => setTimeout(r, 5));
    open--;
    return { answers: { c0: { type: "noul", noul: 0.9 } } };
  } };
  const pool = Array.from({ length: 50 }, (_, i) => claim(`s${i}`));
  const out = await rankForQuery(slow, "q".repeat(5000), pool, 50);
  assert.equal(peak, RERANK_IN_FLIGHT);
  assert.equal(calls.length, RERANK_POOL, "only the first RERANK_POOL claims are scored");
  assert.equal(out.length, RERANK_POOL);
  assert.equal(calls[0].state.query.length, RERANK_QUERY);
});

test("one deadline covers the whole pool, and a failure starts no more requests", async () => {
  let calls = 0;
  const hang: S1Client = { systemOne: () => (calls++, new Promise(() => {})) };
  const t = Date.now();
  assert.equal(await relevance(hang, "q", Array.from({ length: 30 }, (_, i) => claim(`s${i}`)), 30), null);
  assert.ok(Date.now() - t < 1000);
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(calls, RERANK_IN_FLIGHT, "no request started after the deadline");
});

test("KYPP_RERANK=off keeps the write gate and recall's keyword order", async () => {
  assert.equal(s1Client({ AI: {} as any, KYPP_RERANK: "off" })!.rerank, false);
  assert.equal(s1Client({ AI: {} as any })!.rerank, true);
  const { client, calls } = scorer({ a: 0, b: 0.9 });
  const pool = [claim("a"), claim("b")];
  assert.deepEqual(await rankForQuery({ ...client, rerank: false }, "q", pool, 10), pool);
  assert.equal(calls.length, 0);
});

test("rerank drops claims under the floor, sorts by score in bands and keeps keyword order inside a band", () => {
  const pool = ["k0", "k1", "k2", "k3", "k4"];
  // k1 and k3 share the 0.8 band, so keyword order decides; k4 is under the floor
  assert.deepEqual(rerank(pool, [0.35, 0.84, 0.95, 0.81, 0.2], 0.3, 10), ["k2", "k1", "k3", "k0"]);
  assert.deepEqual(rerank(pool, [0.35, 0.84, 0.95, 0.81, 0.2], 0.3, 2), ["k2", "k1"]);
  assert.deepEqual(rerank(pool, [0.1, 0.1, 0.1, 0.1, 0.1], 0.3, 10), [], "nothing relevant returns nothing, not padding");
  assert.deepEqual(rerank(pool, [0.1, 0.1, 0.1, 0.1, 0.1], 0, 3), ["k0", "k1", "k2"], "floor 0 reorders but drops nothing");
});

test("the floor applies only to the model it was tuned on; others reorder without dropping", async () => {
  assert.equal(relevantFloorFor(RELEVANT_FLOOR_MODEL), RELEVANT_FLOOR);
  assert.equal(relevantFloorFor("clef-flash"), 0);
  const warn = console.warn;
  console.warn = () => {};
  try {
    assert.equal(s1Client({ AI: {} as any })!.relevantFloor, RELEVANT_FLOOR);
    assert.equal(s1Client({ AI: {} as any, KYPP_S1_MODEL: "clef-flash" })!.relevantFloor, 0);
  } finally {
    console.warn = warn;
  }
  const untuned = { ...scorer({ a: 0.1, b: 0.2 }).client, relevantFloor: undefined };
  assert.deepEqual((await rankForQuery(untuned, "q", [claim("a"), claim("b")], 10)).map((c) => c.subject), ["b", "a"]);
});

test("a pointer hit stays first and unscored, so the model can't drop the claim the query named", async () => {
  const { client, calls } = scorer({ named: 0, b: 0.9, c: 0.1 });
  const pool = [{ ...claim("named"), pointer: true }, claim("c"), claim("b")];
  assert.deepEqual((await rankForQuery(client, "worker/src/s1.ts", pool, 10)).map((c) => c.subject), ["named", "b"]);
  assert.deepEqual(calls.map((r) => r.state.claims[0].subject), ["c", "b"], "only word matches are scored");
});

test("the model reads the whole claim, so a match late in long content is seen", async () => {
  const { client, calls } = scorer({});
  const long = { subject: "s", content: `${"x ".repeat(1500)}needle` };
  await relevance(client, "needle", [long]);
  assert.equal(calls[0].state.claims[0].content, long.content);
});

test("without an opinion recall keeps the keyword order, cut to the limit", async () => {
  const pool = [claim("a"), claim("b"), claim("c")];
  assert.deepEqual(await rankForQuery(null, "q", pool, 2), pool.slice(0, 2));
  assert.deepEqual(await rankForQuery({ systemOne: async () => { throw new Error("down"); } }, "q", pool, 2), pool.slice(0, 2));
});

// One MCP client over a store of `n` accepted project claims that all match the word "cache".
async function seeded(n: number) {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('kypp','',?)").bind("2026-10-21T00:00:00Z").run();
  const call = (s1: S1Client | null, args: Record<string, unknown>, tool = "recall") => async () => {
    const req = new Request("https://k/mcp", { method: "POST", headers: { "Mcp-Session-Id": "s", "X-Kypp-Project": "kypp" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: tool, arguments: args } }) });
    const { result } = await (await handleMcp(req, db, { user: "owner", agent: "t" }, { s1 })).json() as any;
    return result.content[0].text as string;
  };
  for (let i = 0; i < n; i++) {
    await db.prepare("INSERT INTO memory_claims(id,type,subject,content,scope,project,origin,status,authority,confidence,source_ids,code_refs,metadata,created_at,updated_at)"
      + " VALUES(?,'pitfall',?,?,'project','kypp','kypp','accepted','agent',0.7,'[]','[]','{}',?,?)")
      .bind(`${i}`.padStart(32, "0"), `lesson ${i}`, `cache note ${i}`, "2026-10-21T00:00:00Z", "2026-10-21T00:00:00Z").run();
    await db.prepare("INSERT INTO claims_fts(claim_id, subject, content) VALUES(?,?,?)").bind(`${i}`.padStart(32, "0"), `lesson ${i}`, `cache note ${i}`).run();
  }
  return { call, db };
}
const subjects = (text: string) => [...text.matchAll(/lesson (\d+)/g)].map((m) => Number(m[1]));

test("recall reranks a pool wider than its limit, so a relevant claim past the keyword cut comes back", async () => {
  const { call } = await seeded(30);
  const plain = subjects(await call(null, { query: "cache", limit: 3 })());
  assert.equal(plain.length, 3);
  const target = [...Array(30).keys()].find((i) => !plain.includes(i))!; // a claim keyword order left out
  const { client, calls } = scorer({ [`lesson ${target}`]: 0.95 });
  assert.deepEqual(subjects(await call(client, { query: "cache", limit: 3 })()), [target], "only the relevant claim, nothing padded");
  assert.equal(calls.length, Math.min(30, RERANK_POOL), "the whole pool was scored");
});

test("recall marks a path match as a pointer hit and keeps it under the floor", async () => {
  const { call, db } = await seeded(3);
  await db.prepare("INSERT INTO code_pointers(id,claim_id,entry_ref,project,doc_id,anchor,revision,path,symbol,kind,created_at)"
    + " VALUES('p1',?,'','','','','','worker/src/store.ts','','context',?)").bind("1".padStart(32, "0"), "2026-10-21T00:00:00Z").run();
  const { client } = scorer({}); // every word match scores 0
  assert.deepEqual(subjects(await call(client, { query: "worker/src/store.ts" })()), [1]);
});

test("recall with no query browses without calling the model", async () => {
  const { call } = await seeded(3);
  const { client, calls } = scorer({});
  assert.equal(subjects(await call(client, { query: "", limit: 2 })()).length, 2);
  assert.equal(calls.length, 0);
});
