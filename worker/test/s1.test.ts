import assert from "node:assert/strict";
import { test } from "node:test";
import { type S1Client, judge, s1Client } from "../src/s1.ts";

const draft = { subject: "s", content: "c", project: "p" };
const fake = (answers: Record<string, unknown>): S1Client => ({ systemOne: async () => ({ answers }) });

test("no key means no gate", () => {
  assert.equal(s1Client({}), null);
  assert.equal(s1Client({ TYPESAFE_API_KEY: "k", KYPP_S1: "off" }), null);
});

test("reads keep, a confident type and generality", async () => {
  const v = await judge(fake({
    keep: { type: "noul", noul: 0.04 },
    type: { type: "choice", choice: "pitfall", confidence: 0.8, probabilities: {} },
    general: { type: "noul", noul: 0.9 },
  }), draft);
  assert.deepEqual(v, { keep: 0.04, type: "pitfall", general: 0.9 });
});

test("an unsure type label is ignored", async () => {
  const v = await judge(fake({ type: { type: "choice", choice: "decision", confidence: 0.4, probabilities: {} } }), draft);
  assert.equal(v.type, null);
});

test("fails open on errors and junk", async () => {
  const boom: S1Client = { systemOne: async () => { throw new Error("down"); } };
  assert.deepEqual(await judge(boom, draft), { keep: null, type: null, general: null });
  assert.equal((await judge(fake({ keep: { type: "noul", noul: Number.NaN } }), draft)).keep, null);
  assert.deepEqual(await judge(null, draft), { keep: null, type: null, general: null });
});

test("the deadline is end to end", async () => {
  const hang: S1Client = { systemOne: () => new Promise(() => {}) };
  const t0 = Date.now();
  assert.deepEqual(await judge(hang, draft, 50), { keep: null, type: null, general: null });
  assert.ok(Date.now() - t0 < 1000);
});
