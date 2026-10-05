// End-to-end smoke against `wrangler dev --test-scheduled` with the .dev.vars from README:
//   node test/smoke.mjs [base]
// Covers static-token MCP, two-session promotion through the scheduled pass, and the OAuth flow.
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";

const BASE = process.argv[2] ?? "http://localhost:8787";
const TOKEN = "local-token-abcdefghijklmnop";
const SECRET = "local-owner-passphrase";
const subject = `smoke ${Date.now()}`;

// A stand-in TypeSafe API on :8788 (.dev.vars points the gate at it): status reads as not worth
// keeping, anything "flaky" is confidently a pitfall.
const mock = createServer((req, res) => {
  let raw = "";
  req.on("data", (d) => (raw += d));
  req.on("end", () => {
    const { state } = JSON.parse(raw);
    const flaky = state.content.includes("flaky");
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ model: "mock", usage: { input_tokens: 1, output_tokens: 1 }, answers: {
      keep: { type: "noul", noul: state.content.includes("merged") ? 0.02 : 0.9 },
      type: { type: "choice", choice: flaky ? "pitfall" : "fact", confidence: flaky ? 0.9 : 0.3, probabilities: {} },
      general: { type: "noul", noul: 0.2 },
    } }));
  });
}).listen(8788);

async function rpc(auth, session, method, params, id = 1) {
  const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream", Authorization: `Bearer ${auth}` };
  if (session) headers["Mcp-Session-Id"] = session;
  const r = await fetch(`${BASE}/mcp`, { method: "POST", headers, body: JSON.stringify({ jsonrpc: "2.0", id, method, params }) });
  assert.equal(r.status, 200, `${method}: ${r.status} ${await r.clone().text()}`);
  return { body: await r.json(), session: r.headers.get("mcp-session-id") };
}

async function connect(auth) {
  const { body, session } = await rpc(auth, null, "initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "smoke", version: "0" } });
  assert.ok(body.result.instructions.includes("briefing"));
  assert.ok(session, "initialize must hand out a session id");
  return (name, args) => rpc(auth, session, "tools/call", { name, arguments: args }).then(({ body }) => {
    assert.ok(!body.result.isError, `${name}: ${body.result.content[0].text}`);
    return body.result.content[0].text;
  });
}

// 1. static token: tools list, claim from two sessions, promotion by the scheduled pass
const a = await connect(TOKEN);
const tools = (await rpc(TOKEN, null, "tools/list", {})).body.result.tools.map((t) => t.name);
assert.deepEqual(tools.sort(), ["briefing", "claim", "correct", "expand", "recall"]);
const id1 = await a("claim", { project: "smoke", subject, content: "retry the flaky upload once", type: "pitfall" });
assert.ok(!(await a("briefing", { project: "smoke" })).includes(subject), "a fresh claim is a candidate");
assert.ok((await a("recall", { project: "smoke", query: "flaky upload", include_candidates: true })).includes(id1.slice(0, 8)));
const b = await connect(TOKEN);
await b("claim", { project: "smoke", subject, content: "retry the flaky upload once, then fail", type: "pitfall" });
assert.equal((await fetch(`${BASE}/__scheduled`)).status, 200);
const brief = await b("briefing", { project: "smoke" });
assert.ok(brief.includes(subject) && brief.includes("✓"), `promoted after two sessions:\n${brief}`);
const full = JSON.parse(await b("expand", { handle: brief.split("\n").find((l) => l.includes(subject)).slice(0, 8) }));
assert.equal(full.status, "accepted");
assert.equal(full.authority, "agent");
assert.equal(full.user, "huddles");
// correct lands accepted at agent authority and supersedes the survivor
await b("correct", { project: "smoke", subject, content: "the upload is fixed upstream; no retry" });
const after = await b("recall", { project: "smoke", query: "upload" });
assert.ok(after.includes("no retry") && !after.includes("then fail"), after);

// gate: status is turned away; a defaulted type is relabelled when the model is sure
assert.ok((await b("claim", { project: "smoke", subject: `${subject} status`, content: "PR 12 merged and shipped" })).startsWith("Not stored"));
const typed = JSON.parse(await b("expand", { handle: await b("claim", { project: "smoke", subject: `${subject} ci`, content: "the flaky e2e job needs one rerun" }) }));
assert.equal(typed.type, "pitfall");

// user shelf: visible to its owner in any project, invisible to anyone else
await b("claim", { scope: "user", subject: `${subject} asking`, content: "ask one question in prose, not an options menu" });
assert.ok((await b("recall", { project: "other-repo", query: "options menu", include_candidates: true })).includes("prose"));

// 2. no token → 401 with discovery pointer
const anon = await fetch(`${BASE}/mcp`, { method: "POST", body: "{}" });
assert.equal(anon.status, 401);
assert.ok((anon.headers.get("www-authenticate") ?? "").includes("resource_metadata"));

// 3. OAuth: register, authorize with PKCE + passphrase, exchange, call a tool
const redirect = "http://127.0.0.1:9/callback";
const reg = await (await fetch(`${BASE}/oauth/register`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ client_name: "smoke-client", redirect_uris: [redirect], token_endpoint_auth_method: "none" }),
})).json();
const verifier = randomBytes(32).toString("base64url");
const challenge = createHash("sha256").update(verifier).digest("base64url");
const q = new URLSearchParams({ response_type: "code", client_id: reg.client_id, redirect_uri: redirect, code_challenge: challenge,
  code_challenge_method: "S256", state: "st", scope: "mcp", resource: `${BASE}/mcp` });
const page = await fetch(`${BASE}/authorize?${q}`);
assert.equal(page.status, 200);
const cookie = page.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
const handle = (await page.text()).match(/name="handle" value="([^"]+)"/)[1];
const post = (secret) => fetch(`${BASE}/authorize`, {
  method: "POST", redirect: "manual", headers: { Cookie: cookie, "Content-Type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ handle, secret, decision: "approve" }),
});
assert.equal((await post("wrong")).status, 403);
const ok = await post(SECRET);
assert.equal(ok.status, 302, await ok.clone().text());
const code = new URL(ok.headers.get("location")).searchParams.get("code");
const tok = await (await fetch(`${BASE}/oauth/token`, {
  method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri: redirect, client_id: reg.client_id, code_verifier: verifier, resource: `${BASE}/mcp` }),
})).json();
assert.ok(tok.access_token, JSON.stringify(tok));
const o = await connect(tok.access_token);
const cid = await o("claim", { project: "smoke", subject: `${subject} oauth`, content: "written through OAuth" });
const viaOauth = JSON.parse(await o("expand", { handle: cid }));
assert.equal(viaOauth.user, "owner");
assert.equal(viaOauth.agent, "smoke-client");
assert.ok(!(await o("recall", { project: "smoke", query: "options menu", include_candidates: true })).includes("prose"), "another user's shelf leaked");
mock.close();

console.log("OK — static token, two-session promotion, correct, S1 gate, user shelf, 401 discovery, OAuth sign-in");
