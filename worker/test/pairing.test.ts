// Paired room issuers (memory-scope-decay §11.7): the owner approves a user code on kypp, the
// integration redeems its secret handle once, and the issuer token can only manage its own rooms.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type PairingDeps, handlePair, handlePairs, pairedGrant, pairedIssuer } from "../src/pairing.ts";
import { handleRooms, roomCaller } from "../src/rooms.ts";
import { memoryD1 } from "./d1.ts";

const CONFIGURED = new Map([["huddles", new Set(["d-huddles"])]]);
const NONE = new Map<string, Set<string>>();

const base = "https://k";
const deps: PairingDeps = { base, checkOwner: async (s) => (s === "owner-pass" ? "ok" : "wrong") };

const post = (db: D1Database, path: string, body: unknown, headers: Record<string, string> = {}) =>
  handlePair(new Request(`${base}${path}`, { method: "POST", headers, body: JSON.stringify(body) }), db, deps);

const form = (db: D1Database, path: string, fields: Record<string, string>, handler = handlePair) =>
  handler(new Request(`${base}${path}`, { method: "POST", body: new URLSearchParams(fields) }), db, deps);

async function start(db: D1Database, name = "Huddles workspace Launch") {
  const res = await post(db, "/pair", { name });
  assert.equal(res.status, 201);
  return await res.json() as { handle: string; user_code: string; verification_uri: string; expires_in: number };
}

const redeem = async (db: D1Database, handle: string) => {
  const res = await post(db, "/pair/token", { handle });
  return { status: res.status, body: await res.json() as any };
};

test("a pairing starts with a secret handle, a readable code and the page that shows it", async () => {
  const db = memoryD1();
  const p = await start(db);
  assert.match(p.handle, /^kyph_/);
  assert.match(p.user_code, /^[A-Z2-9]{4}-[A-Z2-9]{4}$/);
  assert.equal(p.verification_uri, `${base}/pair/${p.user_code}`);
  assert.equal(p.expires_in, 600);
  const page = await (await handlePair(new Request(p.verification_uri), db, deps)).text();
  assert.match(page, /Huddles workspace Launch/);
  assert.match(page, new RegExp(p.user_code));
  assert.match(page, /can't verify the name/);
  // The code is spelled out for screen readers, which would read it as a word.
  assert.match(page, /class="sr">[A-Z2-9]( [A-Z2-9]){3}, [A-Z2-9]( [A-Z2-9]){3}</);
  const stored = (await db.prepare("SELECT * FROM pairings").all()).results[0] as Record<string, string>;
  assert.notEqual(stored.handle_digest, p.handle);
});

test("kypp's pages are never cached or framed", async () => {
  const db = memoryD1();
  const p = await start(db);
  for (const res of [await handlePair(new Request(p.verification_uri), db, deps), await handlePairs(new Request(`${base}/pairs`), db, deps)]) {
    assert.equal(res.headers.get("Cache-Control"), "no-store");
    assert.equal(res.headers.get("Content-Security-Policy"), "frame-ancestors 'none'");
    assert.equal(res.headers.get("X-Frame-Options"), "DENY");
  }
});

test("names are escaped on the approval page and must be printable", async () => {
  const db = memoryD1();
  const p = await start(db, "<script>x</script>");
  assert.doesNotMatch(await (await handlePair(new Request(p.verification_uri), db, deps)).text(), /<script>x/);
  assert.equal((await post(db, "/pair", { name: "" })).status, 400);
  assert.equal((await post(db, "/pair", { name: "a\nb" })).status, 400);
});

test("the handle redeems only after the owner approves, and only once", async () => {
  const db = memoryD1();
  const p = await start(db);
  assert.equal((await redeem(db, p.handle)).body.error, "authorization_pending");
  const wrong = await (await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "nope" })).text();
  assert.match(wrong, /Wrong passphrase/);
  assert.equal((await redeem(db, p.handle)).body.error, "authorization_pending");
  assert.match(await (await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "owner-pass" })).text(), /Paired/);
  const first = await redeem(db, p.handle);
  assert.equal(first.status, 200);
  assert.match(first.body.token, /^kypi_/);
  assert.equal(await pairedIssuer(db, first.body.token), first.body.issuer);
  assert.equal((await redeem(db, p.handle)).body.error, "expired_token");
  // A used code's page no longer offers approval.
  assert.equal((await handlePair(new Request(p.verification_uri), db, deps)).status, 410);
});

test("a denied or expired pairing never redeems", async () => {
  const db = memoryD1();
  const denied = await start(db);
  await form(db, `/pair/${denied.user_code}`, { decision: "deny" });
  assert.equal((await redeem(db, denied.handle)).body.error, "access_denied");
  const late = await start(db);
  await db.prepare("UPDATE pairings SET expires_at = '2000-01-01T00:00:00Z' WHERE user_code = ?").bind(late.user_code).run();
  assert.equal((await form(db, `/pair/${late.user_code}`, { decision: "approve", secret: "owner-pass" })).status, 410);
  assert.equal((await redeem(db, late.handle)).body.error, "expired_token");
  assert.equal((await redeem(db, "kyph_guess")).body.error, "expired_token");
});

async function paired(db: D1Database, name = "Huddles workspace Launch") {
  const p = await start(db, name);
  await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "owner-pass" });
  return (await redeem(db, p.handle)).body as { issuer: string; token: string };
}

test("a paired issuer's rooms live in its own namespace", async () => {
  const db = memoryD1();
  const a = await paired(db), b = await paired(db, "Huddles workspace Other");
  const room = async (issuer: string) =>
    await (await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id: "bot-1" }) }), db, issuer, { canLink: false })).json() as any;
  const ra = await room(a.issuer), rb = await room(b.issuer);
  assert.equal(ra.room, `room:${a.issuer}:bot-1`);
  assert.notEqual(ra.room, rb.room);
});

test("unlinking revokes the issuer token and pauses every room it issued, and no other", async () => {
  const db = memoryD1();
  const a = await paired(db), b = await paired(db);
  const room = async (issuer: string) =>
    (await (await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id: "bot-1" }) }), db, issuer, { canLink: false })).json() as any).token as string;
  const ta = await room(a.issuer), tb = await room(b.issuer);
  const res = await handlePair(new Request(`${base}/pair`, { method: "DELETE", headers: { Authorization: `Bearer ${a.token}` } }), db, deps);
  assert.equal(res.status, 200);
  assert.equal(await pairedIssuer(db, a.token), null);
  assert.equal(await roomCaller(db, ta, NONE), null);
  assert.ok(await roomCaller(db, tb, NONE));
  assert.equal((await handlePair(new Request(`${base}/pair`, { method: "DELETE", headers: { Authorization: `Bearer ${a.token}` } }), db, deps)).status, 401);
});

test("the owner lists paired apps and unlinks one with the passphrase", async () => {
  const db = memoryD1();
  const a = await paired(db, "Workspace A"), b = await paired(db, "Workspace B");
  assert.match(await (await form(db, "/pairs", { secret: "nope" }, handlePairs)).text(), /Wrong passphrase/);
  const list = await (await form(db, "/pairs", { secret: "owner-pass" }, handlePairs)).text();
  assert.match(list, /Workspace A/);
  assert.match(list, /Workspace B/);
  assert.doesNotMatch(list, /owner-pass/);
  const after = await (await form(db, "/pairs", { secret: "owner-pass", revoke: a.issuer }, handlePairs)).text();
  assert.match(after, /Unlinked <strong>Workspace A<\/strong>/);
  assert.equal(await pairedIssuer(db, a.token), null);
  assert.equal(await pairedIssuer(db, b.token), b.issuer);
});

test("a paired app can't link its rooms to repos, so it can't open their memory", async () => {
  const db = memoryD1();
  await db.prepare("INSERT INTO projects(name, description, created_at) VALUES('huddles','','t')").run();
  const a = await paired(db);
  const res = await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id: "bot-1", links: ["huddles"] }) }), db, a.issuer, { canLink: false });
  assert.equal(res.status, 403);
  // Refused before any name lookup, so an unregistered name answers the same.
  const probe = await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id: "bot-1", links: ["secret"] }) }), db, a.issuer, { canLink: false });
  assert.equal(probe.status, 403);
});

test("a room whose grant is revoked never signs in, even if it was registered during the revoke", async () => {
  const db = memoryD1();
  const a = await paired(db);
  await handlePair(new Request(`${base}/pair`, { method: "DELETE", headers: { Authorization: `Bearer ${a.token}` } }), db, deps);
  // The race: a registration that passed its issuer check before the revoke committed.
  const late = (await (await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id: "bot-2" }) }), db, a.issuer, { canLink: false })).json() as any).token;
  assert.equal(await roomCaller(db, late, NONE), null);
});

test("a configured issuer's rooms close when the operator removes it", async () => {
  const db = memoryD1();
  const token = (await (await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id: "bot-1" }) }), db, "huddles", { canLink: true, credential: "d-huddles" })).json() as any).token;
  assert.ok(await roomCaller(db, token, CONFIGURED));
  assert.equal(await roomCaller(db, token, NONE), null);
});

const seat = async (db: D1Database, issuer: string, member: string, id = "room-1") =>
  (await (await handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id, member }) }), db, issuer, { canLink: false })).json() as any).token as string;
const unlink = (db: D1Database, token: string) =>
  handlePair(new Request(`${base}/pair`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }), db, deps);

test("a new pairing can continue an unlinked grant: its rooms come back, its old seats don't", async () => {
  const db = memoryD1();
  const old = await paired(db, "Huddles workspace Launch");
  const bot = await seat(db, old.issuer, "bot-a");
  const lane = (await roomCaller(db, bot, NONE))!.room;
  await unlink(db, old.token);
  assert.equal(await roomCaller(db, bot, NONE), null);

  const p = await start(db, "Huddles workspace Launch");
  // The code alone doesn't show which apps were unlinked; the passphrase does.
  assert.doesNotMatch(await (await handlePair(new Request(p.verification_uri), db, deps)).text(), /Continue the memory of/);
  assert.doesNotMatch(await (await form(db, `/pair/${p.user_code}`, { decision: "choose", secret: "nope" })).text(), /Continue the memory of/);
  const pageText = await (await form(db, `/pair/${p.user_code}`, { decision: "choose", secret: "owner-pass" })).text();
  assert.match(pageText, /Continue the memory of/);
  assert.match(pageText, /gives this app every room that earlier link made/);
  const done = await (await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "owner-pass", resume: old.issuer })).text();
  assert.match(done, /continues the room memory of <strong>Huddles workspace Launch<\/strong>/);
  const again = (await redeem(db, p.handle)).body;
  assert.equal(again.issuer, old.issuer);
  assert.equal(again.resumed, true);
  // Old seats stay closed (a bot removed while unlinked can't come back); the app seats its bots again.
  assert.equal(await roomCaller(db, bot, NONE), null);
  assert.equal((await roomCaller(db, await seat(db, again.issuer, "bot-a"), NONE))!.room, lane);
  assert.equal(await pairedIssuer(db, old.token), null);
  assert.equal(await pairedIssuer(db, again.token), old.issuer);
});

test("only an unlinked grant can be continued, and a fresh pairing starts empty", async () => {
  const db = memoryD1();
  const live = await paired(db);
  const p = await start(db);
  assert.doesNotMatch(await (await form(db, `/pair/${p.user_code}`, { decision: "choose", secret: "owner-pass" })).text(), /Continue the memory/);
  const refused = await (await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "owner-pass", resume: live.issuer })).text();
  assert.match(refused, /be continued any more/);
  assert.equal((await redeem(db, p.handle)).body.error, "authorization_pending");
  assert.match(await (await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "owner-pass" })).text(), /new, empty room memory/);
  const fresh = (await redeem(db, p.handle)).body;
  assert.notEqual(fresh.issuer, live.issuer);
  assert.equal(await pairedIssuer(db, live.token), live.issuer);
});

test("two pairings can't both continue the same grant", async () => {
  const db = memoryD1();
  const old = await paired(db);
  await unlink(db, old.token);
  const [p, q] = [await start(db), await start(db)];
  for (const x of [p, q]) await form(db, `/pair/${x.user_code}`, { decision: "approve", secret: "owner-pass", resume: old.issuer });
  assert.equal((await redeem(db, p.handle)).body.issuer, old.issuer);
  assert.equal((await redeem(db, q.handle)).body.error, "access_denied");
});

test("a request that signed in before an unlink and a continue can't seat a bot after them", async () => {
  const db = memoryD1();
  const old = await paired(db);
  // The request authenticates now, and its write lands later.
  const signedIn = (await pairedGrant(db, old.token))!;
  const late = (id: string) => handleRooms(new Request(`${base}/rooms`, { method: "POST", body: JSON.stringify({ id, member: "bot-x" }) }),
    db, signedIn.name, { canLink: false, grant: signedIn.grant });
  await unlink(db, old.token);
  assert.equal((await late("room-1")).status, 401);
  const p = await start(db);
  await form(db, `/pair/${p.user_code}`, { decision: "approve", secret: "owner-pass", resume: old.issuer });
  assert.equal((await redeem(db, p.handle)).body.resumed, true);
  assert.equal((await late("room-2")).status, 401);
  assert.equal(((await db.prepare("SELECT COUNT(*) AS n FROM room_members").first()) as any).n, 0);
  assert.equal(((await db.prepare("SELECT COUNT(*) AS n FROM rooms").first()) as any).n, 0);
  const remove = await handleRooms(new Request(`${base}/rooms/room-1/members/bot-x`, { method: "DELETE" }), db, signedIn.name, { canLink: false, grant: signedIn.grant });
  assert.equal(remove.status, 401);
});
