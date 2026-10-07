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
    const { state, questions } = JSON.parse(raw);
    const text = JSON.stringify(state);
    const flaky = text.includes("flaky");
    const all = {
      keep: { type: "noul", noul: text.includes("merged") ? 0.02 : 0.9 },
      type: { type: "choice", choice: flaky ? "pitfall" : "fact", confidence: flaky ? 0.9 : 0.3, probabilities: {} },
      general: { type: "noul", noul: 0.2 },
      agree: { type: "noul", noul: text.includes("port 2") ? 0.1 : 0.9 },
    };
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ model: "mock", usage: { input_tokens: 1, output_tokens: 1 },
      answers: Object.fromEntries(Object.keys(questions).map((k) => [k, all[k]])) }));
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
await a("claim", { project: "smoke", subject: `${subject} port`, content: "the dev server uses port 1" });
const id1 = await a("claim", { project: "smoke", subject, content: "retry the flaky upload once", type: "pitfall" });
assert.ok(!(await a("briefing", { project: "smoke" })).includes(subject), "a fresh claim is a candidate");
assert.ok((await a("recall", { project: "smoke", query: "flaky upload", include_candidates: true })).includes(id1.slice(0, 8)));
const b = await connect(TOKEN);
await b("claim", { project: "smoke", subject, content: "retry the flaky upload once, then fail", type: "pitfall" });
await b("claim", { project: "smoke", subject: `${subject} port`, content: "the dev server uses port 2" });
assert.equal((await fetch(`${BASE}/__scheduled`)).status, 200);
const brief = await b("briefing", { project: "smoke" });
// ...but two sessions that disagree stay candidates: the S1 judge holds the promotion
assert.ok(!brief.includes(`${subject} port`), "a conflicted subject must not promote");
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

// an unknown project is not an error: the claim lands unsorted and only its own session reads it
const stray = await b("claim", { project: "not-registered", subject: `${subject} stray`, content: "a lesson with nowhere to go yet" });
assert.match(stray, /\nunsorted: /);
assert.ok((await b("recall", { project: "smoke", query: "nowhere", include_candidates: true })).includes("@unsorted"));
assert.ok(!(await a("recall", { project: "smoke", query: "nowhere", include_candidates: true })).includes(stray.slice(0, 8)));

// user scope: visible to its owner in any project, invisible to anyone else
const mine = await b("claim", { scope: "user", subject: `${subject} asking`, content: "ask one question in prose, not an options menu" });
assert.ok((await b("recall", { project: "other-repo", query: "options menu", include_candidates: true })).includes("prose"));

// a forged session header earns no session stamp
const forged = await rpc(TOKEN, "deadbeef0000.0000000000000000", "tools/call",
  { name: "claim", arguments: { project: "smoke", subject: `${subject} forged`, content: "a lesson with a made-up session" } });
assert.deepEqual(JSON.parse(await b("expand", { handle: forged.body.result.content[0].text })).source_ids, []);

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
assert.ok(!(await o("recall", { project: "smoke", query: "options menu", include_candidates: true })).includes("prose"), "another user's scope leaked");
const peek = await rpc(tok.access_token, null, "tools/call", { name: "expand", arguments: { handle: mine } });
assert.ok(peek.body.result.isError && peek.body.result.content[0].text.includes("unknown"), "expand leaked another user's scope");
mock.close();

// 4. passphrase attempts are rate limited
const statuses = [];
for (let i = 0; i < 6; i++) statuses.push((await post("nope")).status);
assert.ok(statuses.includes(429), `no rate limit: ${statuses}`);
const regs = [];
for (let i = 0; i < 4; i++) regs.push((await fetch(`${BASE}/oauth/register`, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ client_name: "flood", redirect_uris: ["http://localhost:9/cb"] }) })).status);
assert.ok(regs.includes(429), `no registration limit: ${regs}`);

console.log("OK — static token, promotion, correct, S1 gate, user scope + expand isolation, signed sessions, 401, OAuth, S1 hold on conflict, rate limits");
