// keycard.ts — accept access tokens minted by a Keycard zone (keycard.ai) on /mcp, beside kypp's own
// OAuth grants and static tokens. Keycard issues short-lived, audience-bound JWTs to agents; kypp
// verifies them with @keycardai/oauth and maps the claims onto a Caller. Unset KEYCARD_ISSUER = off.
import { TokenVerifier } from "@keycardai/oauth/server/tokenVerifier";
import type { Caller } from "./mcp.ts";

export interface KeycardEnv {
  KEYCARD_ISSUER?: string;          // the zone, e.g. https://<zone>.keycard.cloud; unset refuses Keycard tokens
  KEYCARD_OWNER_SUBJECTS?: string;  // "sub,sub": Keycard subjects that read and write as the owner; unset refuses all
}

// A token's `scope` entries of this form pin it to those projects, like a "name@proj" static token.
const PROJECT_SCOPE = "kypp:project:";

// One verifier per (issuer, audience) per isolate: it holds the zone's JWKS cache.
const verifiers = new Map<string, TokenVerifier>();
function verifierFor(issuer: string, audience: string): TokenVerifier {
  const key = `${issuer} ${audience}`;
  let v = verifiers.get(key);
  if (!v) verifiers.set(key, v = new TokenVerifier({ issuer, audience, requiredScopes: ["mcp"] }));
  return v;
}

// The Caller a Keycard token stands for, null for a token kypp refuses, or "unavailable" when the zone
// could not be reached to check it. `audience` is kypp's MCP resource id (<base>/mcp), so a token the
// zone minted for any other resource is refused.
export async function keycardCaller(token: string, env: KeycardEnv, audience: string): Promise<Caller | null | "unavailable"> {
  const issuer = env.KEYCARD_ISSUER?.trim();
  // Why: subjects are compared exactly; a zone's ids are opaque and case can matter.
  const subjects = (env.KEYCARD_OWNER_SUBJECTS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!issuer || !subjects.length) return null;
  let t;
  try {
    t = await verifierFor(issuer, audience).verifyToken(token);
  } catch (err) {
    // Why: the zone's keys or metadata could not be fetched. That is not a bad token: the caller
    // should keep it and retry (503), not sign in again (401).
    console.error("keycard: verification could not complete", String(err));
    return "unavailable";
  }
  if (!t) return null;
  // Why: kypp has one owner. A zone user who is not listed is refused, never given an empty scope.
  if (!t.sub || !subjects.includes(t.sub)) return null;
  const pinned = t.scopes.filter((s) => s.startsWith(PROJECT_SCOPE)).map((s) => s.slice(PROJECT_SCOPE.length)).filter(Boolean);
  return {
    user: "owner",
    // Why: keycardAppId names the application and survives credential rotation; clientId does not.
    agent: `keycard:${t.keycardAppId ?? t.clientId}`,
    ...(pinned.length ? { projects: pinned } : {}),
  };
}
