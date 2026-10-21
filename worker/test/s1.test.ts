import assert from "node:assert/strict";
import { test } from "node:test";
import { DROP_BELOW, DROP_BELOW_MODEL, type S1Client, UNTUNED_DROP_BELOW, gateModel, judge, s1Client } from "../src/s1.ts";

const draft = { subject: "s", content: "c", project: "p" };
const fake = (answers: Record<string, unknown>): S1Client => ({ systemOne: async () => ({ answers }) });

test("no key and no AI binding means no gate", () => {
  assert.equal(s1Client({}), null);
  assert.equal(s1Client({ TYPESAFE_API_KEY: "k", KYPP_S1: "off" }), null);
});

test("without a key the gate runs Clef on the AI binding", async () => {
  const calls: [string, any][] = [];
  const AI = { run: async (m: string, input: any) => (calls.push([m, input]), { model: input.model, answers: { keep: { type: "noul", noul: 0.03 } }, usage: {} }) } as any;
  assert.equal((await judge(s1Client({ AI }), draft)).keep, 0.03);
  assert.equal(calls[0][0], "@cf/cloudflare/clef");
  assert.deepEqual(calls[0][1].state, draft);
  await s1Client({ AI, KYPP_S1_MODEL: "clef-flash" })!.systemOne({ state: draft, questions: {} });
  assert.equal(calls[1][0], "@cf/cloudflare/clef-flash");
  assert.equal(s1Client({ AI, KYPP_S1: "off" }), null);
});

test("the tuned cutoff applies only to the model it was tuned on; others keep the old bar and warn once", () => {
  assert.equal(gateModel({ AI: {} as any }), DROP_BELOW_MODEL);
  assert.equal(gateModel({ AI: {} as any, KYPP_S1_MODEL: "clef-flash" }), "clef-flash");
  assert.equal(gateModel({ TYPESAFE_API_KEY: "k" }), "jev-latest");
  assert.equal(gateModel({ TYPESAFE_API_KEY: "k", TYPESAFE_DEFAULT_MODEL: "clef" }), "clef");
  const warn = console.warn, seen: string[] = [];
  console.warn = (m: string) => seen.push(m);
  try {
    assert.equal(s1Client({ AI: {} as any })!.dropBelow, DROP_BELOW);
    assert.equal(s1Client({ TYPESAFE_API_KEY: "k", TYPESAFE_DEFAULT_MODEL: "clef" })!.dropBelow, DROP_BELOW);
    assert.equal(seen.length, 0);
    assert.equal(s1Client({ TYPESAFE_API_KEY: "k", TYPESAFE_DEFAULT_MODEL: "jev-untuned" })!.dropBelow, UNTUNED_DROP_BELOW);
    assert.equal(s1Client({ AI: {} as any, KYPP_S1_MODEL: "flash-untuned" })!.dropBelow, UNTUNED_DROP_BELOW);
    s1Client({ TYPESAFE_API_KEY: "k", TYPESAFE_DEFAULT_MODEL: "jev-untuned" });
    assert.equal(seen.length, 2);
    assert.match(seen[0], /jev-untuned.*tuned on clef/);
  } finally {
    console.warn = warn;
  }
});

test("reads keep, a confident type and generality", async () => {
  const v = await judge(fake({
    keep: { type: "noul", noul: 0.04 },
    type: { type: "choice", choice: "pitfall", confidence: 0.8, probabilities: {} },
    general: { type: "noul", noul: 0.9 },
  }), draft);
  assert.deepEqual(v, { keep: 0.04, type: "pitfall", general: 0.9, control: null });
});

test("an unsure type label is ignored", async () => {
  const v = await judge(fake({ type: { type: "choice", choice: "decision", confidence: 0.4, probabilities: {} } }), draft);
  assert.equal(v.type, null);
});

test("fails open on errors and junk", async () => {
  const boom: S1Client = { systemOne: async () => { throw new Error("down"); } };
  assert.deepEqual(await judge(boom, draft), { keep: null, type: null, general: null, control: null });
  assert.equal((await judge(fake({ keep: { type: "noul", noul: Number.NaN } }), draft)).keep, null);
  assert.deepEqual(await judge(null, draft), { keep: null, type: null, general: null, control: null });
});

test("the deadline is end to end", async () => {
  const hang: S1Client = { systemOne: () => new Promise(() => {}) };
  const t0 = Date.now();
  assert.deepEqual(await judge(hang, draft, 50), { keep: null, type: null, general: null, control: null });
  assert.ok(Date.now() - t0 < 1000);
});
