// pairing.ts — paired room issuers (memory-scope-decay §11.7). An integration asks to pair and gets a
// secret handle plus a short user code; a person opens kypp's page for that code, checks it matches
// what the integration shows, and the owner approves with the passphrase; the integration then
// redeems its handle once for an issuer token that can only register and revoke its own rooms. The
// owner may instead let it continue a revoked paired grant, whose rooms then work again.
import { digest, mintToken, now, pathSegment } from "./rooms.ts";

export const PAIR_PATH = "/pair";
export const PAIRS_PATH = "/pairs";
const TTL_MS = 10 * 60_000;
const POLL_SECONDS = 5;
// No vowels (no words) and no 0/O/1/I lookalikes.
const CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789";
const CODE = /^[BCDFGHJKLMNPQRSTVWXZ2-9]{4}-[BCDFGHJKLMNPQRSTVWXZ2-9]{4}$/;
const NAME = /^[^\u0000-\u001f\u007f]{1,80}$/;

/** What the page handlers need from index.ts: the owner check (rate limited there) and the origin. */
export interface PairingDeps {
  readonly base: string;
  /** "limited" when the caller used up its attempts; otherwise whether `secret` is the passphrase. */
  readonly checkOwner: (secret: string) => Promise<"ok" | "wrong" | "limited">;
}

const json = (body: unknown, status = 200) => Response.json(body, { status });
const escape = (v: string) => v.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
const page = (title: string, body: string, status = 200) =>
  new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${escape(title)}</title>
<style>body{font:16px system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem}input{font:inherit;padding:.4rem;width:100%}button{font:inherit;padding:.4rem 1rem;margin-right:.5rem}code{font-size:1.4rem;letter-spacing:.1em}.err{color:#b00}li{margin:.5rem 0}</style>
${body}`, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });

const PASSPHRASE = `<p><label>Owner passphrase<br><input type="password" name="secret" autocomplete="current-password" required></label></p>`;
const ownerError = (check: "wrong" | "limited") =>
  check === "limited" ? "Too many attempts. Wait a minute and try again." : "Wrong passphrase.";

function userCode(): string {
  const b = crypto.getRandomValues(new Uint8Array(8));
  const c = [...b].map((x) => CODE_ALPHABET[x % CODE_ALPHABET.length]).join("");
  return `${c.slice(0, 4)}-${c.slice(4)}`;
}

interface Pairing {
  handle_digest: string; user_code: string; name: string; expires_at: string;
  approved_at: string | null; denied_at: string | null; redeemed_at: string | null; resume: string | null;
}

const live = (p: Pairing | null): p is Pairing =>
  !!p && !p.denied_at && !p.redeemed_at && p.expires_at > now();

// Context: doc://kypp/memory-scope-decay@0009#scope-keys-decay — an issuer token only registers and revokes its own rooms; it never reads or writes memory.
/**
 * The live paired grant a token stands for: its issuer name and the credential generation (the
 * token's digest) that writes must still find, or null (unknown, revoked, or not one).
 */
export async function pairedGrant(db: D1Database, token: string): Promise<{ name: string; grant: string } | null> {
  if (!token.startsWith("kypi_")) return null;
  const grant = await digest(token);
  const row = await db.prepare("SELECT name FROM room_issuers WHERE token_digest = ? AND revoked_at IS NULL")
    .bind(grant).first<{ name: string }>();
  return row ? { name: row.name, grant } : null;
}

/** The issuer name a paired issuer token stands for, or null (unknown, revoked, or not one). */
export async function pairedIssuer(db: D1Database, token: string): Promise<string | null> {
  return (await pairedGrant(db, token))?.name ?? null;
}

// Context: doc://kypp/memory-scope-decay@0009#scope-keys-decay — revoking a paired grant stops every token under it but keeps its rooms, so the owner can continue it later.
/** Revokes a paired grant. Its rooms stay; `roomCaller` refuses their tokens while it is revoked. */
async function revokeIssuer(db: D1Database, name: string): Promise<void> {
  await db.prepare("UPDATE room_issuers SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL").bind(now(), name).run();
}

interface Revoked { name: string; label: string; created_at: string; revoked_at: string }
const revokedGrants = (db: D1Database) =>
  db.prepare("SELECT name, label, created_at, revoked_at FROM room_issuers WHERE revoked_at IS NOT NULL ORDER BY revoked_at DESC LIMIT 20")
    .all<Revoked>().then((r) => r.results);

/**
 * `POST /pair {name}` starts a pairing; `POST /pair/token {handle}` redeems it (RFC 8628's
 * `authorization_pending`, `access_denied` and `expired_token` until then); `GET`/`POST
 * /pair/<code>` is the owner's approval page; `DELETE /pair` (Bearer: the issuer token) unlinks.
 * The caller rate-limits `POST /pair`, which anyone can reach.
 */
// Context: doc://kypp/memory-scope-decay@0009#scope-keys-decay — pairing needs both the owner's approval on kypp and the integration's secret handle; codes expire in 10 minutes and redeem once.
export async function handlePair(request: Request, db: D1Database, deps: PairingDeps): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (path === PAIR_PATH && request.method === "POST") return start(request, db, deps);
  if (path === PAIR_PATH && request.method === "DELETE") {
    const token = request.headers.get("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1] ?? "";
    const name = await pairedIssuer(db, token);
    if (!name) return json({ error: "unauthorized" }, 401);
    await revokeIssuer(db, name);
    return json({ issuer: name, revoked: true });
  }
  if (path === `${PAIR_PATH}/token` && request.method === "POST") return redeem(request, db);
  const code = pathSegment(path.slice(PAIR_PATH.length + 1))?.toUpperCase() ?? "";
  if (CODE.test(code) && (request.method === "GET" || request.method === "POST")) return approve(request, db, deps, code);
  return json({ error: "not found" }, 404);
}

async function start(request: Request, db: D1Database, deps: PairingDeps): Promise<Response> {
  let body: { name?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "body must be JSON" }, 400);
  }
  const name = typeof body.name === "string" ? body.name.trim() : "";
  if (!NAME.test(name)) return json({ error: "name must be 1-80 printable characters" }, 400);
  const ts = now();
  await db.prepare("DELETE FROM pairings WHERE expires_at < ? AND redeemed_at IS NULL").bind(ts).run();
  const handle = mintToken("kyph_");
  const expires = new Date(Date.now() + TTL_MS).toISOString();
  for (let attempt = 0; ; attempt++) {
    const code = userCode();
    try {
      await db.prepare("INSERT INTO pairings(handle_digest, user_code, name, created_at, expires_at) VALUES(?,?,?,?,?)")
        .bind(await digest(handle), code, name, ts, expires).run();
      return json({
        handle, user_code: code, verification_uri: `${deps.base}${PAIR_PATH}/${code}`,
        expires_in: TTL_MS / 1000, interval: POLL_SECONDS,
      }, 201);
    } catch (error) {
      if (attempt >= 2) throw error; // a code collision is ~1 in 10^11; three in a row is a real fault
    }
  }
}

async function redeem(request: Request, db: D1Database): Promise<Response> {
  let body: { handle?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_request" }, 400);
  }
  if (typeof body.handle !== "string" || !body.handle.startsWith("kyph_")) return json({ error: "invalid_request" }, 400);
  const hd = await digest(body.handle);
  const p = await db.prepare("SELECT * FROM pairings WHERE handle_digest = ?").bind(hd).first<Pairing>();
  if (!p || p.redeemed_at || p.expires_at <= now()) return json({ error: "expired_token" }, 400);
  if (p.denied_at) return json({ error: "access_denied" }, 400);
  if (!p.approved_at) return json({ error: "authorization_pending" }, 400);
  // Claim the redemption first, so two concurrent polls can't both mint a token.
  const claimed = await db.prepare("UPDATE pairings SET redeemed_at = ? WHERE handle_digest = ? AND redeemed_at IS NULL")
    .bind(now(), hd).run();
  if (!claimed.meta.changes) return json({ error: "expired_token" }, 400);
  const token = mintToken("kypi_");
  if (p.resume) {
    // Only a still-revoked grant is taken over, so a live one (or one another pairing just
    // continued) can't be.
    const ts = now();
    // The rooms come back, but no seat does: a bot removed while the app was unlinked couldn't be
    // unseated then, so the app seats its current bots again. One batch, so the seats are closed
    // before the new token can be used.
    const [resumed] = await db.batch([
      db.prepare("UPDATE room_issuers SET token_digest = ?, label = ?, revoked_at = NULL WHERE name = ? AND revoked_at IS NOT NULL")
        .bind(await digest(token), p.name, p.resume),
      db.prepare(`UPDATE room_members SET revoked_at = ?, updated_at = ? WHERE revoked_at IS NULL
        AND room IN (SELECT key FROM rooms WHERE integration = ?)
        AND EXISTS (SELECT 1 FROM room_issuers WHERE name = ? AND token_digest = ?)`)
        .bind(ts, ts, p.resume, p.resume, await digest(token)),
    ]);
    if (!resumed.meta.changes) return json({ error: "access_denied" }, 400);
    return json({ issuer: p.resume, token, token_type: "Bearer", resumed: true });
  }
  const issuer = `pair-${[...crypto.getRandomValues(new Uint8Array(6))].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
  await db.prepare("INSERT INTO room_issuers(name, label, token_digest, created_at) VALUES(?,?,?,?)")
    .bind(issuer, p.name, await digest(token), now()).run();
  return json({ issuer, token, token_type: "Bearer" });
}

async function approve(request: Request, db: D1Database, deps: PairingDeps, code: string): Promise<Response> {
  const p = await db.prepare("SELECT * FROM pairings WHERE user_code = ?").bind(code).first<Pairing>();
  if (!live(p) || p.approved_at) {
    return page("kypp pairing", "<h1>This code has expired or was already used.</h1><p>Start linking again from the app.</p>", 410);
  }
  let error = "";
  // Unlinked grants are listed only after the passphrase, so a code alone doesn't reveal them.
  let earlier: Revoked[] | null = null;
  if (request.method === "POST") {
    const form = await request.formData();
    const decision = form.get("decision");
    if (decision !== "approve" && decision !== "choose") {
      await db.prepare("UPDATE pairings SET denied_at = ? WHERE user_code = ?").bind(now(), code).run();
      return page("kypp pairing", "<h1>Pairing denied.</h1><p>The app can't create rooms with this code.</p>");
    }
    const check = await deps.checkOwner(String(form.get("secret") ?? ""));
    const chosen = String(form.get("resume") ?? "");
    if (check !== "ok") error = ownerError(check);
    else if (decision === "choose") earlier = await revokedGrants(db);
    else {
      const resume = chosen ? (await revokedGrants(db)).find((g) => g.name === chosen)?.name ?? null : null;
      if (chosen && !resume) error = "That earlier link can't be continued any more.";
      else {
        await db.prepare("UPDATE pairings SET approved_at = ?, resume = ? WHERE user_code = ? AND approved_at IS NULL").bind(now(), resume, code).run();
        return page("kypp pairing", `<h1>Paired.</h1><p>Go back to ${escape(p.name)}; it finishes linking on its own.</p>`);
      }
    }
  }
  // Context: doc://kypp/memory-scope-decay@0009#scope-keys-decay — only a revoked paired grant can be continued, and the page says it hands over that grant's room memory.
  const choices = earlier === null ? "" : `<fieldset><legend>Room memory</legend>
<p><label><input type="radio" name="resume" value="" checked style="width:auto"> Start new, empty room memory</label></p>
${earlier.map((g) => `<p><label><input type="radio" name="resume" value="${escape(g.name)}" style="width:auto"> Continue the memory of
<strong>${escape(g.label)}</strong> (paired ${escape(g.created_at.slice(0, 10))}, unlinked ${escape(g.revoked_at.slice(0, 10))})</label></p>`).join("")}
${earlier.length ? "" : "<p>No unlinked apps to continue.</p>"}
<p>Continuing gives this app every room that earlier link made and the memory in them; the app seats
its bots again. Only continue the same app you unlinked. Enter the passphrase again to approve.</p></fieldset>`;
  const buttons = earlier === null
    ? `<button name="decision" value="approve">Approve</button><button name="decision" value="choose">Continue an earlier link…</button><button name="decision" value="deny" formnovalidate>Deny</button>`
    : `<button name="decision" value="approve">Approve</button><button name="decision" value="deny" formnovalidate>Deny</button>`;
  return page("kypp pairing", `<h1>Let ${escape(p.name)} create room memory?</h1>
<p>Check that this code matches the one the app shows:</p>
<p><code>${escape(code)}</code></p>
<p>The app named itself; kypp can't verify the name. Approving lets it create private memory lanes
for its chat rooms, which it can't link to your repos. It can't read or write any other memory, and you can unlink it at
<a href="${escape(deps.base)}${PAIRS_PATH}">${escape(deps.base)}${PAIRS_PATH}</a>.</p>
${error ? `<p class="err">${escape(error)}</p>` : ""}
<form method="post">
${choices}
${PASSPHRASE}
<p>${buttons}</p>
</form>`);
}

/** `GET /pairs` asks for the passphrase; `POST /pairs` lists paired grants and revokes one if asked. */
// Context: doc://kypp/memory-scope-decay@0009#scope-keys-decay — the owner can revoke any paired grant, which pauses every room it issued.
export async function handlePairs(request: Request, db: D1Database, deps: PairingDeps): Promise<Response> {
  const form = (error = "") => page("kypp paired apps", `<h1>Paired apps</h1>
${error ? `<p class="err">${escape(error)}</p>` : ""}
<form method="post">${PASSPHRASE}
<p><button>Show paired apps</button></p></form>`);
  if (request.method === "GET") return form();
  if (request.method !== "POST") return json({ error: "GET or POST" }, 405);
  const data = await request.formData();
  const secret = String(data.get("secret") ?? "");
  const check = await deps.checkOwner(secret);
  if (check !== "ok") return form(ownerError(check));
  const revoke = data.get("revoke");
  if (typeof revoke === "string" && revoke) await revokeIssuer(db, revoke);
  const { results } = await db.prepare("SELECT name, label, created_at FROM room_issuers WHERE revoked_at IS NULL ORDER BY created_at")
    .all<{ name: string; label: string; created_at: string }>();
  // The passphrase is asked again to unlink, never echoed back into the page.
  const rows = results.map((r) => `<li><label><input type="radio" name="revoke" value="${escape(r.name)}" required style="width:auto">
<strong>${escape(r.label)}</strong>, paired ${escape(r.created_at.slice(0, 10))}</label></li>`).join("");
  return page("kypp paired apps", `<h1>Paired apps</h1>
${typeof revoke === "string" && revoke ? "<p>Unlinked. Its rooms' memory is kept; a later pairing can continue it.</p>" : ""}
${rows ? `<form method="post"><ul>${rows}</ul>
${PASSPHRASE}
<p><button>Unlink</button></p></form>` : "<p>No apps are paired.</p>"}`);
}
