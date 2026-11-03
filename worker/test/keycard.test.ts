import assert from "node:assert/strict";
import { test } from "node:test";
import { keycardCaller } from "../src/keycard.ts";

// A fake Keycard zone: RFC 8414 metadata and a JWKS, served through a stubbed global fetch.
const ISS = "https://zone-test.keycard.cloud";
const AUD = "https://kypp.example.workers.dev/mcp";
const OWNER = "usr_owner";
const ENV = { KEYCARD_ISSUER: ISS, KEYCARD_OWNER_SUBJECTS: `usr_other, ${OWNER}` };
const enc = (v: unknown) => Buffer.from(JSON.stringify(v)).toString("base64url");

const { privateKey, publicKey } = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid: "k1", use: "sig", alg: "RS256" };
delete (jwk as any).key_ops;

let zoneUp = true;
let hits = 0;
globalThis.fetch = (async (input: string | URL | Request) => {
  const url = String(input instanceof Request ? input.url : input);
  hits++;
  if (!zoneUp) return new Response("down", { status: 502 });
  if (url === `${ISS}/.well-known/oauth-authorization-server`) return Response.json({ issuer: ISS, jwks_uri: `${ISS}/.well-known/jwks.json` });
  if (url === `${ISS}/.well-known/jwks.json`) return Response.json({ keys: [jwk] });
  return new Response("not found", { status: 404 });
}) as typeof fetch;

const now = () => Math.floor(Date.now() / 1000);
async function token(over: Record<string, unknown> = {}, kid = "k1") {
  const claims = { iss: ISS, aud: AUD, sub: OWNER, client_id: "cli_1", keycard_app_id: "app_ci", scope: "mcp", iat: now(), exp: now() + 300, ...over };
  const input = `${enc({ alg: "RS256", typ: "at+jwt", kid })}.${enc(claims)}`;
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(input));
  return `${input}.${Buffer.from(sig).toString("base64url")}`;
}

test("a zone token for this resource and a listed subject reads as the owner, named by its app", async () => {
  assert.deepEqual(await keycardCaller(await token(), ENV, AUD), { user: "owner", agent: "keycard:app_ci" });
});

test("kypp:project scopes pin the token to those projects", async () => {
  const t = await token({ scope: "mcp kypp:project:resume kypp:project:kypp" });
  assert.deepEqual(await keycardCaller(t, ENV, AUD), { user: "owner", agent: "keycard:app_ci", projects: ["resume", "kypp"] });
});

test("refused: another resource's token, an unlisted or differently cased subject, no mcp scope, expired", async () => {
  for (const over of [{ aud: "https://other.example/mcp" }, { sub: "usr_stranger" }, { sub: OWNER.toUpperCase() }, { scope: "read" }, { exp: now() - 120 }]) {
    assert.equal(await keycardCaller(await token(over), ENV, AUD), null, JSON.stringify(over));
  }
});

test("off unless both the issuer and an owner subject are set", async () => {
  const t = await token();
  assert.equal(await keycardCaller(t, {}, AUD), null);
  assert.equal(await keycardCaller(t, { KEYCARD_ISSUER: ISS }, AUD), null);
});

test("a token from another issuer is refused before any fetch", async () => {
  const before = hits;
  assert.equal(await keycardCaller(await token({ iss: "https://evil.example" }), ENV, AUD), null);
  assert.equal(hits, before);
});

test("an unreachable zone is 'unavailable', not a refusal", async () => {
  zoneUp = false;
  try {
    assert.equal(await keycardCaller(await token({}, "k-rotated"), ENV, AUD), "unavailable");
  } finally { zoneUp = true; }
});
