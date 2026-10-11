import assert from "node:assert/strict";
import { test } from "node:test";
import { SYSTEM_ONE_PATH, handleSystemOne } from "../src/systemone.ts";

const TOKEN = "tok-0123456789abcdef";
const tokenName = async (t: string) => (t === TOKEN ? "brief-ci" : null);
const body = { model: "clef", state: { decision: "d", diff: "x" }, questions: { conflict: { type: "noul", instructions: "?" } } };

// What typesafe-sdk sends: POST <base>/v1/systemone, Bearer key, JSON {state, model, questions}.
const req = (b: unknown = body, auth: string | null = `Bearer ${TOKEN}`, method = "POST") =>
  new Request(`https://kypp.example${SYSTEM_ONE_PATH}`, {
    method,
    headers: { "Content-Type": "application/json", ...(auth ? { Authorization: auth } : {}) },
    body: method === "POST" ? (typeof b === "string" ? b : JSON.stringify(b)) : undefined,
  });

function stubAI(reply: unknown = { answers: { conflict: { type: "noul", noul: 0.12 } }, usage: { input_tokens: 40, output_tokens: 1 } }) {
  const calls: [string, any][] = [];
  const ai = { run: async (m: string, input: any) => { calls.push([m, input]); if (reply instanceof Error) throw reply; return reply; } } as any;
  return { ai, calls };
}

test("answers in the TypeSafe response shape from Clef on the AI binding", async () => {
  const { ai, calls } = stubAI();
  const res = await handleSystemOne(req(), ai, tokenName);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { model: "clef", usage: { input_tokens: 40, output_tokens: 1 }, answers: { conflict: { type: "noul", noul: 0.12 } } });
  assert.equal(calls[0][0], "@cf/cloudflare/clef");
  assert.deepEqual(calls[0][1], { model: "clef", state: body.state, questions: body.questions });
});

test("clef-flash is passed through, a missing model is clef, anything else is refused", async () => {
  const { ai, calls } = stubAI({ model: "clef-flash-2026", answers: {} });
  const res = await handleSystemOne(req({ ...body, model: "clef-flash" }), ai, tokenName);
  assert.deepEqual(await res.json(), { model: "clef-flash-2026", usage: {}, answers: {} });
  await handleSystemOne(req({ state: body.state, questions: body.questions }), ai, tokenName);
  assert.deepEqual(calls.map((c) => c[0]), ["@cf/cloudflare/clef-flash", "@cf/cloudflare/clef"]);
  for (const model of ["jev-latest", "@cf/meta/llama", 3]) {
    assert.equal((await handleSystemOne(req({ ...body, model }), ai, tokenName)).status, 400);
  }
  assert.equal(calls.length, 2);
});

test("only a kypp API token gets in", async () => {
  const { ai, calls } = stubAI();
  for (const auth of [null, "Bearer wrong-token-0000000000", `Basic ${TOKEN}`, "Bearer "]) {
    assert.equal((await handleSystemOne(req(body, auth), ai, tokenName)).status, 401);
  }
  assert.equal((await handleSystemOne(req(body, `bearer ${TOKEN}`), ai, tokenName)).status, 200);
  assert.equal(calls.length, 1);
});

test("no AI binding is 503; bad bodies are 400; model errors are 502", async () => {
  assert.equal((await handleSystemOne(req(), undefined, tokenName)).status, 503);
  const { ai } = stubAI();
  for (const b of ["not json", [], { questions: body.questions }, { state: 1, questions: {} }, { state: 1, questions: [] }]) {
    assert.equal((await handleSystemOne(req(b), ai, tokenName)).status, 400);
  }
  assert.equal((await handleSystemOne(req(), stubAI(new Error("down")).ai, tokenName)).status, 502);
  assert.equal((await handleSystemOne(req(), stubAI({ text: "hi" }).ai, tokenName)).status, 502);
  assert.equal((await handleSystemOne(req(body, `Bearer ${TOKEN}`, "GET"), ai, tokenName)).status, 405);
});
