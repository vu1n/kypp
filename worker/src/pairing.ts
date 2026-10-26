// pairing.ts — paired room issuers. An integration asks to pair and gets a
// secret handle plus a short user code; a person opens kypp's page for that code, checks it matches
// what the integration shows, and the owner approves with the passphrase; the integration then
// redeems its handle once for an issuer token that can only register and revoke its own rooms. The
// owner may instead let it continue a revoked paired grant, whose rooms then work again.
import { escape, htmlResponse, icon, passphrase, render } from "./page.ts";
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
const page = (base: string, title: string, body: string, status = 200) => htmlResponse(render(base, title, body), status);

const ownerError = (check: "wrong" | "limited") =>
  check === "limited" ? "Too many attempts. Wait a minute and try again." : "Wrong passphrase.";
const day = (iso: string) => escape(iso.slice(0, 10));
/** The code spelled out for a screen reader, which would otherwise read it as a word. */
const spoken = (code: string) => code.split("-").map((half) => [...half].join(" ")).join(", ");

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

// An issuer token only registers and revokes its own rooms; it never reads or writes memory.
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

// Revoking a paired grant stops every token under it but keeps its rooms, so the owner can continue it later.
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
// Pairing needs both the owner's approval on kypp and the integration's secret handle; codes expire in 10 minutes and redeem once.
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
  // A person opening a cut-off or mistyped link gets a page; API callers get JSON.
  if (request.method === "GET" && path.startsWith(`${PAIR_PATH}/`)) {
    return page(deps.base, "Link not valid", `<h1>${icon.x}This pairing link isn't valid</h1>
<p>Check that you opened the whole link the app showed.</p>
<p class="lede">Otherwise, start linking again from the app.</p>`, 404);
  }
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
    return page(deps.base, "Code expired", `<h1>${icon.x}This code can't be used</h1>
<p>It has expired or was already used.</p>
<p class="lede">Start linking again from the app.</p>`, 410);
  }
  const name = escape(p.name);
  let error = "";
  // Unlinked grants are listed only after the passphrase, so a code alone doesn't reveal them.
  let earlier: Revoked[] | null = null;
  if (request.method === "POST") {
    const form = await request.formData();
    const decision = form.get("decision");
    if (decision !== "approve" && decision !== "choose") {
      await db.prepare("UPDATE pairings SET denied_at = ? WHERE user_code = ?").bind(now(), code).run();
      return page(deps.base, "Pairing denied", `<h1>${icon.x}Pairing denied</h1><p>${name} can't create rooms with this code.</p>`);
    }
    const check = await deps.checkOwner(String(form.get("secret") ?? ""));
    const chosen = String(form.get("resume") ?? "");
    if (check !== "ok") {
      error = ownerError(check);
      if (check === "wrong" && chosen) error += " Choose “Continue an earlier link…” again to pick it.";
    } else if (decision === "choose") earlier = await revokedGrants(db);
    else {
      const resume = chosen ? (await revokedGrants(db)).find((g) => g.name === chosen) ?? null : null;
      if (chosen && !resume) error = "That earlier link can't be continued any more.";
      else {
        await db.prepare("UPDATE pairings SET approved_at = ?, resume = ? WHERE user_code = ? AND approved_at IS NULL").bind(now(), resume?.name ?? null, code).run();
        return page(deps.base, "Paired", `<h1>${icon.check}Paired</h1>
<p>${resume ? `${name} continues the room memory of <strong>${escape(resume.label)}</strong>.` : `${name} starts with new, empty room memory.`}
Go back to ${name}; it finishes linking on its own.</p>
<p class="lede">You can unlink it on <a href="${PAIRS_PATH}">Paired apps</a>.</p>`);
      }
    }
  }
  // Only a revoked paired grant can be continued, and the page says it hands over that grant's room memory.
  const choices = !earlier?.length ? "" : `<fieldset><legend>Room memory</legend>
<p>Continuing gives this app every room that earlier link made and the memory in them; the app seats
its bots again. Only continue the same app you unlinked.</p>
<ul class="choices"><li><label><input type="radio" name="resume" value="" checked><span>Start new, empty room memory</span></label></li>
${earlier.map((g) => `<li><label><input type="radio" name="resume" value="${escape(g.name)}"><span>Continue the memory of
<strong>${escape(g.label)}</strong><small>paired ${day(g.created_at)}, unlinked ${day(g.revoked_at)}</small></span></label></li>`).join("")}</ul></fieldset>`;
  const none = earlier && !earlier.length
    ? `<p class="note info" role="status">${icon.info}<span>No unlinked apps to continue. Approving starts new, empty room memory.</span></p>` : "";
  return page(deps.base, "Approve pairing", `<h1>Let “${name}” create room memory?</h1>
<p>The app named itself; kypp can't verify the name.</p>
<p>Check that this code matches the one the app shows. If it doesn't, choose Deny.</p>
<p class="code"><span aria-hidden="true">${escape(code)}</span><span class="sr">${spoken(code)}</span></p>
<p class="lede">Codes expire 10 minutes after the app asks to pair.</p>
<p>Approving lets it create private memory for its chat rooms, which it can't link to your repos. It can't
read or write any other memory, and you can unlink it on <a href="${PAIRS_PATH}">Paired apps</a>.</p>
<form method="post">
${choices}${none}
${passphrase({ error, hint: earlier ? "Enter the passphrase again to approve." : "" })}
<div class="actions"><button class="primary" name="decision" value="approve">Approve</button><button name="decision" value="deny" formnovalidate>Deny</button></div>
${earlier ? "" : `<button class="quiet" name="decision" value="choose">Continue an earlier link…</button>`}
</form>`);
}

/** `GET /pairs` asks for the passphrase; `POST /pairs` lists paired grants and revokes one if asked. */
// The owner can revoke any paired grant, which pauses every room it issued.
export async function handlePairs(request: Request, db: D1Database, deps: PairingDeps): Promise<Response> {
  const form = (error = "") => page(deps.base, "Paired apps", `<h1>Paired apps</h1>
<p class="lede">Apps you paired can create memory for their chat rooms. Enter the passphrase to see them or unlink one.</p>
<form method="post">${passphrase({ error, focus: true })}
<div class="actions"><button class="primary">Show paired apps</button></div></form>`);
  if (request.method === "GET") return form();
  if (request.method !== "POST") return json({ error: "GET or POST" }, 405);
  const data = await request.formData();
  const secret = String(data.get("secret") ?? "");
  const check = await deps.checkOwner(secret);
  if (check !== "ok") return form(ownerError(check));
  const revoke = data.get("revoke");
  let unlinked = "";
  if (typeof revoke === "string" && revoke) {
    await revokeIssuer(db, revoke);
    const row = await db.prepare("SELECT label FROM room_issuers WHERE name = ?").bind(revoke).first<{ label: string }>();
    unlinked = `<p class="note ok" role="status">${icon.check}<span>Unlinked <strong>${escape(row?.label ?? "the app")}</strong>. Its rooms' memory is kept; a later pairing can continue it.</span></p>`;
  }
  const { results } = await db.prepare("SELECT name, label, created_at FROM room_issuers WHERE revoked_at IS NULL ORDER BY created_at")
    .all<{ name: string; label: string; created_at: string }>();
  // The passphrase is asked again to unlink, never echoed back into the page.
  const rows = results.map((r) => `<li><label><input type="radio" name="revoke" value="${escape(r.name)}" required><span>
<strong>${escape(r.label)}</strong><small>paired ${day(r.created_at)}</small></span></label></li>`).join("");
  return page(deps.base, "Paired apps", `<h1>Paired apps</h1>
${unlinked}
${rows ? `<form method="post"><fieldset><legend>Choose an app to unlink</legend><ul class="choices">${rows}</ul></fieldset>
${passphrase()}
<div class="actions"><button class="danger">Unlink</button></div></form>`
    : `<p>No apps are paired. Pairing starts from the app: it shows a code and a link to approve it here.</p>`}`);
}
