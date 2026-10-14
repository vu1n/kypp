// rooms.ts — room lanes (memory-scope-decay §11): registering a chat room's bot as its own principal,
// and recognising its bearer token on /mcp. The lane rules themselves live in mcp.ts and store.ts.
import type { Caller } from "./mcp.ts";

export const ROOMS_PATH = "/rooms";
const ROOM_ID = /^[A-Za-z0-9._-]{1,128}$/;
const MAX_LINKS = 16;

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

// Context: doc://kypp/memory-scope-decay@0007#scope-keys-decay — a room token names its room; kypp keeps only the digest, and a revoked room, or one whose issuer grant is gone, never signs in.
/**
 * The caller a room token stands for. `configured` lists the operator's live issuer names; a
 * paired issuer must still have an unrevoked grant. Checking the grant here, not only cascading
 * its revoke, also closes a room registered while its grant was being revoked.
 */
export async function roomCaller(db: D1Database, token: string, configured: readonly string[]): Promise<Caller | null> {
  if (!token.startsWith("kypr_")) return null;
  const row = await db.prepare(`SELECT r.key, r.integration, i.name AS paired, i.revoked_at AS grant_revoked
    FROM rooms r LEFT JOIN room_issuers i ON i.name = r.integration
    WHERE r.token_digest = ? AND r.revoked_at IS NULL`)
    .bind(await digest(token)).first<{ key: string; integration: string; paired: string | null; grant_revoked: string | null }>();
  if (!row) return null;
  const live = row.paired !== null ? row.grant_revoked === null : configured.includes(row.integration);
  return live ? { user: roomIdentity(row.key), agent: roomIdentity(row.key), room: row.key } : null;
}

const json = (body: unknown, status = 200) => Response.json(body, { status });

/**
 * `POST /rooms {id, links?}` registers a room for `issuer` (or rotates its token and replaces its
 * links when it is already registered and live) and returns the token once. `DELETE /rooms/<id>`
 * revokes it. A revoked room stays revoked: re-registering its id is refused, so a later bot never
 * inherits a removed one's memory.
 */
// Context: doc://kypp/memory-scope-decay@0007#scope-keys-decay — the integration that manages the room sets its links; the bot never does.
export async function handleRooms(request: Request, db: D1Database, issuer: string, opts: { canLink: boolean }): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (request.method === "DELETE" && path.startsWith(`${ROOMS_PATH}/`)) {
    const id = pathSegment(path.slice(ROOMS_PATH.length + 1));
    if (id === null || !ROOM_ID.test(id)) return json({ error: "bad room id" }, 400);
    const key = `${issuer}:${id}`;
    await db.prepare("UPDATE rooms SET revoked_at = ?, updated_at = ? WHERE key = ? AND revoked_at IS NULL").bind(now(), now(), key).run();
    return json({ room: roomIdentity(key), revoked: true });
  }
  if (request.method !== "POST" || path !== ROOMS_PATH) return json({ error: "POST /rooms or DELETE /rooms/<id>" }, 405);
  let body: { id?: unknown; links?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "body must be JSON" }, 400);
  }
  const id = typeof body.id === "string" ? body.id : "";
  if (!ROOM_ID.test(id)) return json({ error: "id must be 1-128 chars of letters, digits, . _ -" }, 400);
  const links = body.links ?? [];
  if (!Array.isArray(links) || links.length > MAX_LINKS || !links.every((l) => typeof l === "string")) {
    return json({ error: `links must be a list of at most ${MAX_LINKS} registered project names` }, 400);
  }
  const unique = [...new Set(links as string[])];
  // Why: a paired grant is approved by a self-named app, and links open a repo's memory to the
  // room, so only an operator-configured issuer may set them (checked before any name lookup).
  if (unique.length && !opts.canLink) return json({ error: "a paired app can't link rooms to repos" }, 403);
  const { results } = unique.length
    ? await db.prepare(`SELECT name FROM projects WHERE name IN (${unique.map(() => "?").join(",")})`).bind(...unique).all()
    : { results: [] };
  const known = new Set(results.map((r) => r.name as string));
  const unknown = unique.filter((l) => !known.has(l));
  // Why: naming the missing links is fine here; the caller is the operator's integration, not a room.
  if (unknown.length) return json({ error: `not registered projects: ${unknown.join(", ")}` }, 400);

  const key = `${issuer}:${id}`;
  const existing = await db.prepare("SELECT revoked_at FROM rooms WHERE key = ?").bind(key).first<{ revoked_at: string | null }>();
  if (existing?.revoked_at) return json({ error: "this room was revoked; register the bot under a new id" }, 409);
  const token = mintToken(), ts = now();
  await db.batch([
    existing
      ? db.prepare("UPDATE rooms SET token_digest = ?, updated_at = ? WHERE key = ?").bind(await digest(token), ts, key)
      : db.prepare("INSERT INTO rooms(key, integration, token_digest, created_at, updated_at) VALUES(?,?,?,?,?)")
        .bind(key, issuer, await digest(token), ts, ts),
    db.prepare("DELETE FROM room_links WHERE room = ?").bind(key),
    ...unique.map((p) => db.prepare("INSERT INTO room_links(room, project, created_at) VALUES(?,?,?)").bind(key, p, ts)),
  ]);
  return json({ room: roomIdentity(key), token, links: unique }, existing ? 200 : 201);
}
