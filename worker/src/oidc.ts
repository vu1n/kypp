// oidc.ts — verify a GitHub Actions OIDC token (RS256 JWT) with WebCrypto, so CI can call
// /v1/systemone with no shared secret. The audience is the Worker's public URL (KYPP_PUBLIC_URL with
// no trailing slash): a token minted for another service or another kypp deployment is refused.
export const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
export const GITHUB_JWKS_URL = `${GITHUB_ISSUER}/.well-known/jwks`;

const SKEW_S = 60;               // clock skew allowed on exp/nbf/iat
const JWKS_TTL_MS = 60 * 60_000; // refresh the key set hourly
const REFETCH_GAP_MS = 60_000;   // an unknown kid refetches at most once a minute
const FETCH_TIMEOUT_MS = 3_000;

export interface OidcCaller { repository: string; workflow: string; owner: string }
export interface OidcOptions { audience: string; owners: string[] }
type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

const dec = new TextDecoder();
function b64url(s: string): Uint8Array<ArrayBuffer> {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}
const jsonPart = (s: string): unknown => JSON.parse(dec.decode(b64url(s)));
const isObject = (v: unknown): v is Record<string, any> => typeof v === "object" && v !== null && !Array.isArray(v);

export const owners = (list: string | undefined): string[] =>
  (list ?? "").split(",").map((o) => o.trim().toLowerCase()).filter(Boolean);

export const looksLikeJwt = (token: string) => /^[\w-]+\.[\w-]+\.[\w-]+$/.test(token);

// One verifier per isolate holds the JWKS cache; tests make their own with a stub fetch and clock.
export function oidcVerifier(fetcher: Fetcher = (u, i) => fetch(u, i), now: () => number = Date.now) {
  let keys = new Map<string, CryptoKey>();
  let fetchedAt = -Infinity;

  async function refresh(): Promise<void> {
    fetchedAt = now();
    const res = await fetcher(GITHUB_JWKS_URL, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
    if (!res.ok) throw new Error(`jwks fetch returned ${res.status}`);
    const body: unknown = await res.json();
    const next = new Map<string, CryptoKey>();
    for (const k of isObject(body) && Array.isArray(body.keys) ? body.keys : []) {
      if (!isObject(k) || k.kty !== "RSA" || typeof k.kid !== "string" || (k.use && k.use !== "sig")) continue;
      // Only the public parts: extra JWK members (x5c, key_ops) can make importKey refuse the key.
      next.set(k.kid, await crypto.subtle.importKey("jwk", { kty: "RSA", n: k.n, e: k.e, alg: "RS256", ext: true },
        { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]));
    }
    keys = next;
  }

  async function keyFor(kid: string): Promise<CryptoKey | undefined> {
    const stale = now() - fetchedAt > JWKS_TTL_MS;
    if (stale || (!keys.has(kid) && now() - fetchedAt > REFETCH_GAP_MS)) {
      try { await refresh(); } catch (err) { console.error("oidc: jwks fetch failed", String(err)); }
    }
    return keys.get(kid);
  }

  // The caller the token names, or null when anything about it is off.
  async function verify(token: string, opts: OidcOptions): Promise<OidcCaller | null> {
    if (!opts.owners.length || !opts.audience || !looksLikeJwt(token)) return null;
    const [h, p, s] = token.split(".");
    let header: unknown, claims: unknown;
    try { header = jsonPart(h); claims = jsonPart(p); } catch { return null; }
    if (!isObject(header) || header.alg !== "RS256" || typeof header.kid !== "string" || !isObject(claims)) return null;
    const key = await keyFor(header.kid);
    if (!key) return null;
    let sig: Uint8Array<ArrayBuffer>;
    try { sig = b64url(s); } catch { return null; }
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, sig, new TextEncoder().encode(`${h}.${p}`));
    if (!ok) return null;
    const t = now() / 1000;
    const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (claims.iss !== GITHUB_ISSUER || !aud.includes(opts.audience)) return null;
    if (typeof claims.exp !== "number" || claims.exp + SKEW_S < t) return null;
    if (typeof claims.nbf === "number" && claims.nbf - SKEW_S > t) return null;
    if (typeof claims.iat === "number" && claims.iat - SKEW_S > t) return null;
    const owner = typeof claims.repository_owner === "string" ? claims.repository_owner.toLowerCase() : "";
    if (!owner || !opts.owners.includes(owner)) return null;
    return { owner, repository: String(claims.repository ?? ""), workflow: String(claims.workflow_ref ?? claims.workflow ?? "") };
  }

  return { verify };
}
