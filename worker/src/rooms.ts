// rooms.ts — room lanes (memory-scope-decay §11): registering a chat room's bot as its own principal,
// and recognising its bearer token on /mcp. The lane rules themselves live in mcp.ts and store.ts.
import type { Caller } from "./mcp.ts";
import { D1Store } from "./store.ts";

export const ROOMS_PATH = "/rooms";
const ROOM_ID = /^[A-Za-z0-9._-]{1,128}$/;
const MAX_LINKS = 16;
// Compared as text against now(), so it must be the same fixed-width UTC form.
const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/;

/** The principal a room's claims are keyed by, in the `user` column (as a person's are). */
export const roomIdentity = (key: string) => `room:${key}`;

export const now = () => new Date().toISOString();

export async function digest(token: string): Promise<string> {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token)));
  return [...d].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function mintToken(prefix = "kypr_"): string {
  const b = crypto.getRandomValues(new Uint8Array(32));
  return `${prefix}${btoa(String.fromCharCode(...b)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "")}`;
}

/** A path segment, or null when it isn't valid percent-encoding. */
export function pathSegment(raw: string): string | null {
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

// Context: doc://kypp/memory-scope-decay@0010#scope-keys-decay — a member token names its room; kypp keeps only the digest, and a revoked member or room, or one whose issuer grant is revoked, never signs in.
/**
 * The caller a member token stands for: its room's lane. `configured` maps the operator's live
 * issuer names to the digests of their current secrets, and a configured issuer's seat signs in
 * only while the secret that made it is one of them; a paired issuer must still have an unrevoked grant. Checking
 * the grant here is what pauses a revoked grant's rooms without revoking them.
 */
export async function roomCaller(db: D1Database, token: string, configured: ReadonlyMap<string, ReadonlySet<string>>): Promise<Caller | null> {
  if (!token.startsWith("kypr_")) return null;
  const row = await db.prepare(`SELECT r.key, r.integration, m.issuer_digest, i.name AS paired, i.revoked_at AS grant_revoked
    FROM room_members m JOIN rooms r ON r.key = m.room LEFT JOIN room_issuers i ON i.name = r.integration
    WHERE m.token_digest = ? AND m.revoked_at IS NULL AND r.revoked_at IS NULL
      AND (m.expires_at IS NULL OR m.expires_at > ?)`)
    .bind(await digest(token), now()).first<{ key: string; integration: string; issuer_digest: string | null; paired: string | null; grant_revoked: string | null }>();
  if (!row) return null;
  // Context: doc://kypp/memory-scope-decay@0010#scope-keys-decay — a configured issuer's seat dies with the secret that made it, so re-enabling the name under a new secret never revives old seats.
  const live = row.paired !== null ? row.grant_revoked === null
    : row.issuer_digest !== null && !!configured.get(row.integration)?.has(row.issuer_digest);
  return live ? { user: roomIdentity(row.key), agent: roomIdentity(row.key), room: row.key } : null;
}

const json = (body: unknown, status = 200) => Response.json(body, { status });
const revoke = (db: D1Database, sql: string, ...keys: string[]) => db.prepare(sql).bind(now(), now(), ...keys).run();

/**
 * `POST /rooms {id, member?, expires_at?, links?}` registers a room for `issuer` if it is new,
 * seats `member` (default "default") with a new token, which it returns once and which stops
 * working after `expires_at` when given, and replaces the room's links when
 * `links` is given. Seating a member again rotates its token. `DELETE /rooms/<id>/members/<member>`
 * removes one bot; `DELETE /rooms/<id>` closes the room for good, so re-registering it is refused.
 */
// Context: doc://kypp/memory-scope-decay@0010#scope-keys-decay — the integration that manages the room sets its links; the bot never does.
/** `credential` is the digest of a configured issuer's secret, recorded on each seat it makes. */
export async function handleRooms(request: Request, db: D1Database, issuer: string, opts: { canLink: boolean; grant?: string; credential?: string; linkable?: string[] }): Promise<Response> {
  const path = new URL(request.url).pathname;
  // Why: a paired caller was authenticated before its body was read, and an unlink and a continue
  // can land in between. Every write re-checks, in the same statement, that the grant still holds
  // the credential generation (token digest) the caller signed in with.
  // Context: doc://kypp/memory-scope-decay@0010#scope-keys-decay — a revoked or continued grant's old token can't seat bots, even mid-request.
  const held = opts.grant === undefined ? { sql: "", args: [] as string[] }
    : { sql: " AND EXISTS (SELECT 1 FROM room_issuers WHERE name = ? AND token_digest = ? AND revoked_at IS NULL)", args: [issuer, opts.grant] };
  if (request.method === "DELETE" && path.startsWith(`${ROOMS_PATH}/`)) {
    const parts = path.slice(ROOMS_PATH.length + 1).split("/").map(pathSegment);
    const [id, kind, member] = parts;
    const valid = (v: string | null | undefined): v is string => typeof v === "string" && ROOM_ID.test(v);
    if (!valid(id) || !(parts.length === 1 || (parts.length === 3 && kind === "members" && valid(member)))) {
      return json({ error: "DELETE /rooms/<id> or /rooms/<id>/members/<member>" }, 400);
    }
    const key = `${issuer}:${id}`;
    if (parts.length === 1) {
      const r = await revoke(db, `UPDATE rooms SET revoked_at = ?, updated_at = ? WHERE key = ? AND revoked_at IS NULL${held.sql}`, key, ...held.args);
      return r.meta.changes || opts.grant === undefined || await stillHeld(db, issuer, opts.grant)
        ? json({ room: roomIdentity(key), revoked: true }) : json({ error: "unauthorized" }, 401);
    }
    const r = await revoke(db, `UPDATE room_members SET revoked_at = ?, updated_at = ? WHERE room = ? AND member = ? AND revoked_at IS NULL${held.sql}`, key, member as string, ...held.args);
    return r.meta.changes || opts.grant === undefined || await stillHeld(db, issuer, opts.grant)
      ? json({ room: roomIdentity(key), member, revoked: true }) : json({ error: "unauthorized" }, 401);
  }
  if (request.method !== "POST" || path !== ROOMS_PATH) return json({ error: "POST /rooms or DELETE /rooms/<id>[/members/<member>]" }, 405);
  let body: { id?: unknown; member?: unknown; expires_at?: unknown; links?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "body must be JSON" }, 400);
  }
  const id = typeof body.id === "string" ? body.id : "";
  const member = body.member === undefined ? "default" : typeof body.member === "string" ? body.member : "";
  if (!ROOM_ID.test(id) || !ROOM_ID.test(member)) return json({ error: "id and member must be 1-128 chars of letters, digits, . _ -" }, 400);
  const expires = body.expires_at === undefined ? null : typeof body.expires_at === "string" && ISO.test(body.expires_at) ? body.expires_at : "";
  if (expires === "" || (expires && expires <= now())) return json({ error: "expires_at must be a future UTC time like 2026-01-01T00:00:00.000Z" }, 400);
  const links = body.links;
  if (links !== undefined && (!Array.isArray(links) || links.length > MAX_LINKS || !links.every((l) => typeof l === "string"))) {
    return json({ error: `links must be a list of at most ${MAX_LINKS} registered project names` }, 400);
  }
  const asked = links === undefined ? null : [...new Set(links as string[])];
  // Why: a paired grant is approved by a self-named app, and links open a repo's memory to the
  // room, so only an operator-configured issuer may set them (checked before any name lookup).
  if (asked?.length && !opts.canLink) return json({ error: "a paired app can't link rooms to repos" }, 403);
  // Why: a bare repo name is ambiguous across GitHub owners, so a link may name `owner/repo`, and
  // it then links the project only when that project rolls up to the owner's org (§10.1).
  const owned = await ownedNames(db, (asked ?? []).filter((l) => l.includes("/")));
  const resolve = (l: string) => (l.includes("/") ? owned.get(l) : l);
  const unique = asked === null ? null : [...new Set(asked.flatMap((l) => resolve(l) ?? []))];
  // Why: a room reads its links, so a pinned issuer linking outside its pin would step around it.
  const outside = opts.linkable ? (asked ?? []).filter((l) => !opts.linkable!.includes(resolve(l) ?? l)) : [];
  if (outside.length) return json({ error: `this token may not link rooms to: ${outside.join(", ")}` }, 403);
  const { results } = unique?.length
    ? await db.prepare(`SELECT name FROM projects WHERE name IN (${unique.map(() => "?").join(",")})`).bind(...unique).all()
    : { results: [] };
  const known = new Set(results.map((r) => r.name as string));
  const unknown = (asked ?? []).filter((l) => !known.has(resolve(l) ?? l));
  // Why: naming the missing links is fine here; the caller is the operator's integration, not a room.
  if (unknown.length) return json({ error: `not registered projects: ${unknown.join(", ")}` }, 400);

  const key = `${issuer}:${id}`;
  const existing = await db.prepare("SELECT revoked_at FROM rooms WHERE key = ?").bind(key).first<{ revoked_at: string | null }>();
  if (existing?.revoked_at) return json({ error: "this room was closed; register it under a new id" }, 409);
  const token = mintToken(), ts = now(), td = await digest(token);
  const [, seated] = await db.batch([
    db.prepare(`INSERT INTO rooms(key, integration, created_at, updated_at) SELECT ?,?,?,? WHERE 1${held.sql} ON CONFLICT(key) DO NOTHING`)
      .bind(key, issuer, ts, ts, ...held.args),
    db.prepare(`INSERT INTO room_members(room, member, token_digest, created_at, updated_at, expires_at, issuer_digest) SELECT ?,?,?,?,?,?,? WHERE 1${held.sql}
      ON CONFLICT(room, member) DO UPDATE SET token_digest = excluded.token_digest, updated_at = excluded.updated_at,
        expires_at = excluded.expires_at, issuer_digest = excluded.issuer_digest, revoked_at = NULL`)
      .bind(key, member, td, ts, ts, expires, opts.credential ?? null, ...held.args),
    ...(unique === null ? [] : [
      db.prepare("DELETE FROM room_links WHERE room = ?").bind(key),
      ...unique.map((p) => db.prepare("INSERT INTO room_links(room, project, created_at) VALUES(?,?,?)").bind(key, p, ts)),
    ]),
  ]);
  if (!seated.meta.changes) return json({ error: "unauthorized" }, 401);
  return json({ room: roomIdentity(key), member, token, ...(unique === null ? {} : { links: unique }) }, existing ? 200 : 201);
}

/**
 * For each `owner/repo` link, the project named `repo` when `owner` is the org at the top of its
 * roll-up (GitHub names, matched without case), through groups in between, unless a project is
 * registered under the link itself. Any other link is left out.
 */
async function ownedNames(db: D1Database, qualified: string[]) {
  const store = new D1Store(db);
  const owned = new Map<string, string>();
  for (const link of qualified) {
    // A project registered under the path form itself (`vu1n/kypp`) is that project, as before.
    if (await db.prepare("SELECT 1 FROM projects WHERE name = ?").bind(link).first()) {
      owned.set(link, link);
      continue;
    }
    const [owner, repo, ...rest] = link.split("/");
    if (!owner || !repo || rest.length) continue;
    const row = await db.prepare("SELECT name FROM projects WHERE lower(name) = lower(?)").bind(repo).first<{ name: string }>();
    if (!row) continue;
    const orgs = await store.orgs(await store.ancestors(row.name));
    if ([...orgs].some((org) => org.toLowerCase() === owner.toLowerCase())) owned.set(link, row.name);
  }
  return owned;
}

const stillHeld = async (db: D1Database, name: string, grant: string) =>
  !!(await db.prepare("SELECT 1 FROM room_issuers WHERE name = ? AND token_digest = ? AND revoked_at IS NULL").bind(name, grant).first());
