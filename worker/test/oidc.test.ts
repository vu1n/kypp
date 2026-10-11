import assert from "node:assert/strict";
import { test } from "node:test";
import { GITHUB_ISSUER, GITHUB_JWKS_URL, oidcVerifier, owners } from "../src/oidc.ts";
import { handleSystemOne } from "../src/systemone.ts";

const AUD = "https://kypp.example.workers.dev";
const OPTS = { audience: AUD, owners: ["vu1n"] };
const enc = (v: unknown) => Buffer.from(typeof v === "string" ? v : JSON.stringify(v)).toString("base64url");

async function keypair(kid: string) {
  const { privateKey, publicKey } = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  // GitHub's JWKS carries x5c/x5t too; keep an extra member so the import path ignores it.
  const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid, use: "sig", alg: "RS256", x5t: "x" };
  delete (jwk as any).key_ops;
  return { kid, privateKey, jwk };
}

async function sign(key: { kid: string; privateKey: CryptoKey }, claims: Record<string, unknown>, header: Record<string, unknown> = {}) {
  const input = `${enc({ alg: "RS256", typ: "JWT", kid: key.kid, ...header })}.${enc(claims)}`;
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key.privateKey, new TextEncoder().encode(input));
  return `${input}.${Buffer.from(sig).toString("base64url")}`;
}

const NOW = 1_800_000_000_000;
const claims = (over: Record<string, unknown> = {}) => ({
  iss: GITHUB_ISSUER, aud: AUD, exp: NOW / 1000 + 300, nbf: NOW / 1000 - 5, iat: NOW / 1000 - 5,
  repository: "vu1n/brief", repository_owner: "vu1n", workflow: "brief", workflow_ref: "vu1n/brief/.github/workflows/brief.yml@refs/pull/1/merge", ...over,
});

// A stub JWKS endpoint: `serve` is what it returns now; `hits` counts fetches.
function jwksStub(serve: any[]) {
  const state = { serve, hits: 0, urls: [] as string[] };
  const fetcher = async (url: string) => {
    state.hits++; state.urls.push(url);
    return new Response(JSON.stringify({ keys: state.serve }), { status: 200 });
  };
  return { state, fetcher };
}

test("a valid GitHub Actions token names its repository and workflow", async () => {
  const k = await keypair("k1");
  const { state, fetcher } = jwksStub([k.jwk]);
  const v = oidcVerifier(fetcher, () => NOW);
  assert.deepEqual(await v.verify(await sign(k, claims()), OPTS),
    { owner: "vu1n", repository: "vu1n/brief", workflow: "vu1n/brief/.github/workflows/brief.yml@refs/pull/1/merge" });
  assert.deepEqual(state.urls, [GITHUB_JWKS_URL]);
  // Cached: a second token does not refetch, and the owner match ignores case.
  assert.ok(await v.verify(await sign(k, claims({ repository_owner: "Vu1n" })), OPTS));
  assert.ok(await v.verify(await sign(k, claims({ aud: ["other", AUD] })), OPTS));
  assert.equal(state.hits, 1);
});

test("a bad signature, wrong iss/aud/owner, or an expired token is refused", async () => {
  const k = await keypair("k1");
  const imposter = await keypair("k1"); // same kid, different key
  const v = oidcVerifier(jwksStub([k.jwk]).fetcher, () => NOW);
  const good = await sign(k, claims());
  const [h, p] = good.split(".");
  const cases = [
    await sign(imposter, claims()),
    `${h}.${enc(claims({ repository_owner: "vu1n", repository: "vu1n/other" }))}.${good.split(".")[2]}`, // payload swapped
    await sign(k, claims({ iss: "https://token.actions.githubusercontent.com/" })),
    await sign(k, claims({ aud: `${AUD}/` })),
    await sign(k, claims({ aud: "kypp" })),
    await sign(k, claims({ repository_owner: "someone-else" })),
    await sign(k, claims({ repository_owner: undefined })),
    await sign(k, claims({ exp: NOW / 1000 - 61 })),
    await sign(k, claims({ exp: undefined })),
    await sign(k, claims({ nbf: NOW / 1000 + 120 })),
    await sign(k, claims(), { alg: "none" }),
    await sign(k, claims(), { alg: "HS256" }),
    `${h}.${p}.`,
    "not-a-jwt",
  ];
  for (const [i, t] of cases.entries()) assert.equal(await v.verify(t, OPTS), null, `case ${i}`);
  // Small skew is tolerated.
  assert.ok(await v.verify(await sign(k, claims({ exp: NOW / 1000 - 30, nbf: NOW / 1000 + 30 })), OPTS));
});

test("no listed owners or no audience refuses OIDC without fetching keys", async () => {
  const k = await keypair("k1");
  const { state, fetcher } = jwksStub([k.jwk]);
  const v = oidcVerifier(fetcher, () => NOW);
  const t = await sign(k, claims());
  assert.equal(await v.verify(t, { audience: AUD, owners: owners(undefined) }), null);
  assert.equal(await v.verify(t, { audience: AUD, owners: owners(" , ") }), null);
  assert.equal(await v.verify(t, { audience: "", owners: ["vu1n"] }), null);
  assert.equal(state.hits, 0);
  assert.deepEqual(owners("vu1n, Acme ,"), ["vu1n", "acme"]);
});

test("an unknown kid refetches the key set, at most once a minute", async () => {
  const k1 = await keypair("k1");
  const k2 = await keypair("k2");
  const { state, fetcher } = jwksStub([k1.jwk]);
  let now = NOW;
  const v = oidcVerifier(fetcher, () => now);
  assert.ok(await v.verify(await sign(k1, claims()), OPTS));
  assert.equal(state.hits, 1);
  // GitHub rotates in k2. Within a minute of the last fetch an unknown kid does not refetch.
  state.serve = [k1.jwk, k2.jwk];
  now += 30_000;
  assert.equal(await v.verify(await sign(k2, claims()), OPTS), null);
  assert.equal(state.hits, 1);
  now += 31_000;
  assert.ok(await v.verify(await sign(k2, claims()), OPTS));
  assert.equal(state.hits, 2);
  // A failing fetch leaves the cached keys in place.
  const down = oidcVerifier(async () => new Response("no", { status: 503 }), () => NOW);
  assert.equal(await down.verify(await sign(k1, claims()), OPTS), null);
});

test("/v1/systemone logs the OIDC caller on model failure, never the token", async () => {
  const k = await keypair("k1");
  const v = oidcVerifier(jwksStub([k.jwk]).fetcher, () => NOW);
  const token = await sign(k, claims());
  const authenticate = async (t: string) => {
    const c = await v.verify(t, OPTS);
    return c ? `oidc ${c.repository} ${c.workflow}` : null;
  };
  const req = (auth: string) => new Request("https://kypp.example/v1/systemone", {
    method: "POST", headers: { Authorization: auth, "Content-Type": "application/json" },
    body: JSON.stringify({ model: "clef", state: {}, questions: { q: { type: "noul" } } }),
  });
  const ai = { run: async () => { throw new Error("down"); } } as any;
  const logged: unknown[][] = [];
  const orig = console.error;
  console.error = (...a: unknown[]) => { logged.push(a); };
  try {
    assert.equal((await handleSystemOne(req(`Bearer ${token}`), ai, authenticate)).status, 502);
    assert.equal((await handleSystemOne(req(`Bearer ${await sign(k, claims({ repository_owner: "x" }))}`), ai, authenticate)).status, 401);
  } finally {
    console.error = orig;
  }
  assert.equal(logged.length, 1);
  const line = logged[0].map(String).join(" ");
  assert.match(line, /oidc vu1n\/brief vu1n\/brief\/\.github\/workflows\/brief\.yml/);
  assert.ok(!line.includes(token.split(".")[2]));
});
