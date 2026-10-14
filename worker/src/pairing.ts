// pairing.ts — paired room issuers (memory-scope-decay §11.7). An integration asks to pair and gets a
// secret handle plus a short user code; a person opens kypp's page for that code, checks it matches
// what the integration shows, and the owner approves with the passphrase; the integration then
// redeems its handle once for an issuer token that can only register and revoke its own rooms.
import { digest, mintToken, now } from "./rooms.ts";

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
  approved_at: string | null; denied_at: string | null; redeemed_at: string | null;
}

const live = (p: Pairing | null): p is Pairing =>
  !!p && !p.denied_at && !p.redeemed_at && p.expires_at > now();

// Context: doc://kypp/memory-scope-decay@0007#scope-keys-decay — an issuer token only registers and revokes its own rooms; it never reads or writes memory.
/** The issuer name a paired issuer token stands for, or null (unknown, revoked, or not one). */
export async function pairedIssuer(db: D1Database, token: string): Promise<string | null> {
  if (!token.startsWith("kypi_")) return null;
  const row = await db.prepare("SELECT name FROM room_issuers WHERE token_digest = ? AND revoked_at IS NULL")
    .bind(await digest(token)).first<{ name: string }>();
  return row?.name ?? null;
}

/** Revokes a paired grant and every room it issued. */
async function revokeIssuer(db: D1Database, name: string): Promise<void> {
  const ts = now();
  await db.batch([
    db.prepare("UPDATE room_issuers SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL").bind(ts, name),
    db.prepare("UPDATE rooms SET revoked_at = ?, updated_at = ? WHERE integration = ? AND revoked_at IS NULL").bind(ts, ts, name),
  ]);
}

/**
 * `POST /pair {name}` starts a pairing; `POST /pair/token {handle}` redeems it (RFC 8628's
 * `authorization_pending`, `access_denied` and `expired_token` until then); `GET`/`POST
 * /pair/<code>` is the owner's approval page; `DELETE /pair` (Bearer: the issuer token) unlinks.
 * The caller rate-limits `POST /pair`, which anyone can reach.
 */
// Context: doc://kypp/memory-scope-decay@0007#scope-keys-decay — pairing needs both the owner's approval on kypp and the integration's secret handle; codes expire in 10 minutes and redeem once.
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
  const code = decodeURIComponent(path.slice(PAIR_PATH.length + 1)).toUpperCase();
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
  const issuer = `pair-${[...crypto.getRandomValues(new Uint8Array(6))].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
  const token = mintToken("kypi_");
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
  if (request.method === "POST") {
    const form = await request.formData();
    if (form.get("decision") !== "approve") {
      await db.prepare("UPDATE pairings SET denied_at = ? WHERE user_code = ?").bind(now(), code).run();
      return page("kypp pairing", "<h1>Pairing denied.</h1><p>The app can't create rooms with this code.</p>");
    }
    const check = await deps.checkOwner(String(form.get("secret") ?? ""));
    if (check === "ok") {
      await db.prepare("UPDATE pairings SET approved_at = ? WHERE user_code = ? AND approved_at IS NULL").bind(now(), code).run();
      return page("kypp pairing", `<h1>Paired.</h1><p>Go back to ${escape(p.name)}; it finishes linking on its own.</p>`);
    }
    error = ownerError(check);
  }
  return page("kypp pairing", `<h1>Let ${escape(p.name)} create room memory?</h1>
<p>Check that this code matches the one the app shows:</p>
<p><code>${escape(code)}</code></p>
<p>The app named itself; kypp can't verify the name. Approving lets it create private memory lanes
for its chat rooms. It can't read or write any other memory, and you can unlink it at
<a href="${escape(deps.base)}${PAIRS_PATH}">${escape(deps.base)}${PAIRS_PATH}</a>.</p>
${error ? `<p class="err">${escape(error)}</p>` : ""}
<form method="post">
${PASSPHRASE}
<p><button name="decision" value="approve">Approve</button><button name="decision" value="deny" formnovalidate>Deny</button></p>
</form>`);
}

/** `GET /pairs` asks for the passphrase; `POST /pairs` lists paired grants and revokes one if asked. */
// Context: doc://kypp/memory-scope-decay@0007#scope-keys-decay — the owner can revoke any paired grant, which revokes every room it issued.
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
${typeof revoke === "string" && revoke ? "<p>Unlinked. Its rooms' memory lanes are closed.</p>" : ""}
${rows ? `<form method="post"><ul>${rows}</ul>
${PASSPHRASE}
<p><button>Unlink</button></p></form>` : "<p>No apps are paired.</p>"}`);
}
