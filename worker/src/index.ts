// index.ts — kypp's hosted store: one Worker that is both the OAuth authorization server and the MCP
// resource at /mcp, over D1. Sign-in is a single owner passphrase (KYPP_OWNER_SECRET); headless
// clients can present a named static token from KYPP_API_TOKENS instead.
import { AuthorizationError, OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { type Caller, handleMcp } from "./mcp.ts";
import { defrag } from "./defrag.ts";
import { type Policy } from "./memory.ts";
import { type S1Env, s1Client } from "./s1.ts";
import { D1Store } from "./store.ts";
import { looksLikeJwt, oidcVerifier, owners } from "./oidc.ts";
import { SYSTEM_ONE_PATH, handleSystemOne } from "./systemone.ts";
import { OPERATOR_PATH, handleOperator } from "./operator.ts";
import { sameSecret } from "./secret.ts";
import { ROOMS_PATH, digest, handleRooms, roomCaller } from "./rooms.ts";
import { type ApiToken, apiTokens, tokenPins } from "./tokens.ts";
import { PAIRS_PATH, PAIR_PATH, type PairingDeps, handlePair, handlePairs, pairedGrant } from "./pairing.ts";
import { escape, htmlResponse, icon, passphrase, render } from "./page.ts";

export interface Env extends S1Env {
  DB: D1Database;
  OAUTH_KV: KVNamespace;
  OAUTH_PROVIDER: any; // injected by OAuthProvider for the default handler
  KYPP_PUBLIC_URL?: string; // e.g. https://kypp.<account>.workers.dev; unset = the origin the request came in on
  KYPP_OWNER_SECRET: string;
  CONSENT_SECRET: string;
  KYPP_API_TOKENS?: string;
  KYPP_ROOM_ISSUERS?: string; // "huddles,...": KYPP_API_TOKENS names that may register chat rooms (memory-scope-decay §11); unset refuses all
  KYPP_S1_TOKENS?: string; // "name:token,..." for /v1/systemone only; never accepted on /mcp
  KYPP_OPERATOR_TOKEN?: string; // the operator's kypp CLI on /v1/operator only; unset = that route is 404
  KYPP_OIDC_OWNERS?: string; // "owner,owner": GitHub owners whose Actions OIDC tokens may call /v1/systemone; unset refuses OIDC
  KYPP_RECUR_GAP_MINUTES?: string;  // default 60: how far apart the supporting sessions must be
  KYPP_CORRECT?: string;            // "off" hides the trust-based `correct` tool
  AUTH_LIMIT?: RateLimit;     // passphrase attempts
  REGISTER_LIMIT?: RateLimit; // OAuth client registrations and pairing starts, which anyone can attempt
}
type RateLimit = { limit(o: { key: string }): Promise<{ success: boolean }> };

// Promotion is automatic in every scope; memory is for agents, so nothing waits on a person.
export function policyFrom(env: Pick<Env, "KYPP_RECUR_GAP_MINUTES">): Policy {
  const gap = Number(env.KYPP_RECUR_GAP_MINUTES ?? 60);
  return { k: 2, minGapMs: (Number.isFinite(gap) && gap >= 0 ? gap : 60) * 60_000, autoAccept: () => true };
}

// False when this caller has used up its passphrase attempts (no binding configured = no limit).
async function allowAttempt(request: Request, env: Env, limiter = env.AUTH_LIMIT, kind = "pass"): Promise<boolean> {
  if (!limiter) return true;
  const ip = request.headers.get("CF-Connecting-IP") ?? "local";
  return (await limiter.limit({ key: `${kind}:${ip}` })).success;
}
const tooMany = () => new Response("Too many attempts. Wait a minute and try again.", { status: 429 });

interface ConsentDetails { clientName: string; clientDomain?: string; redirectHost: string; redirectIsLoopback: boolean }

function consentPage(base: string, d: ConsentDetails, handle: string, error = ""): string {
  const name = escape(d.clientName);
  return render(base, "Allow app access", `<h1>Allow “${name}” to use your kypp memory?</h1>
<p>${d.clientDomain ? `Published by <strong>${escape(d.clientDomain)}</strong>.` : "This app registered itself; its name is not verified."}</p>
<dl class="grant"><dt>Access goes to</dt><dd class="target">${escape(d.redirectHost)}</dd>
<dt>It can</dt><dd>Read and write all of your memory, in every project</dd></dl>
${d.redirectIsLoopback ? `<p class="note" role="note">${icon.warning}<span><strong>This sends access to an app on your computer.</strong> Continue only if you just started signing in from it.</span></p>` : ""}
<form method="post">
<input type="hidden" name="handle" value="${escape(handle)}">
${passphrase({ error })}
<div class="actions"><button class="primary" name="decision" value="approve">Allow</button><button name="decision" value="deny" formnovalidate>Deny</button></div>
</form>`);
}

// A sign-in that can't go on: an expired or reused handle, a bad request, or a refused retry with no
// request to show again. The OAuth library's handle errors end "; start again", which the last line
// already says.
const signInError = (base: string, message: string, status: number) => htmlResponse(render(base, "Sign-in stopped",
  `<h1>${icon.stop}This sign-in can't continue</h1><p>${escape(message.replace(/; start again$/, "").replace(/[^.!?]$/, "$&."))}</p>
<p class="lede">Start signing in again from the app you came from.</p>`), status);

async function showConsent(oauth: any, req: unknown, base: string, error = "", status = 200): Promise<Response> {
  const details = await oauth.describeConsent(req);
  const consent = await oauth.beginConsent(req);
  return htmlResponse(consentPage(base, details, consent.handle, error), status, consent.headers);
}

// A refused Allow shows the form again with the error. The browser posts back to the same
// /authorize?… URL, so the request is parsed again and the page gets a fresh handle that stands for
// exactly what it shows; a post without that query gets the error alone.
async function retryConsent(oauth: any, request: Request, base: string, error: string, status: number): Promise<Response> {
  let req: unknown;
  try {
    req = await oauth.parseAuthRequest(request);
  } catch {
    return signInError(base, error, status);
  }
  return showConsent(oauth, req, base, error, status);
}

async function authorize(request: Request, env: Env, base: string): Promise<Response> {
  const oauth = env.OAUTH_PROVIDER;
  try {
    if (request.method === "GET") return await showConsent(oauth, await oauth.parseAuthRequest(request), base);
    const form = await request.formData();
    const handle = String(form.get("handle"));
    if (form.get("decision") !== "approve") {
      const denied = await oauth.denyConsent(request, handle);
      return new Response(null, { status: 302, headers: denied.headers });
    }
    if (!(await allowAttempt(request, env))) return await retryConsent(oauth, request, base, "Too many attempts. Wait a minute and try again.", 429);
    if (!env.KYPP_OWNER_SECRET || !(await sameSecret(String(form.get("secret") ?? ""), env.KYPP_OWNER_SECRET))) {
      return await retryConsent(oauth, request, base, "Wrong passphrase.", 403);
    }
    const approved = await oauth.approveConsent(request, handle, { scope: ["mcp"] });
    const client = await oauth.lookupClient(approved.request.clientId);
    const { redirectTo } = await oauth.completeAuthorization({
      request: approved.request,
      userId: "owner",
      metadata: {},
      scope: approved.request.scope,
      props: { user: "owner", agent: client?.clientName ?? approved.request.clientId } satisfies Caller,
    });
    approved.headers.set("Location", redirectTo);
    return new Response(null, { status: 302, headers: approved.headers });
  } catch (error) {
    if (error instanceof AuthorizationError && (error as any).redirectTo) return Response.redirect((error as any).redirectTo, 302);
    if (error instanceof AuthorizationError) return signInError(base, (error as any).description ?? "The sign-in request isn't valid.", 400);
    throw error;
  }
}

// The static token presented as `token`, or null.
async function apiToken(token: string, list: string | undefined): Promise<ApiToken | null> {
  for (const [secret, t] of apiTokens(list)) if (await sameSecret(token, secret)) return t;
  return null;
}
async function tokenName(token: string, list: string | undefined): Promise<string | null> {
  return (await apiToken(token, list))?.name ?? null;
}

const oidc = oidcVerifier();

// Context: doc://kypp/memory-scope-decay@0010#scope-keys-decay — only an issuer grant registers rooms (an operator-configured name, or a paired issuer token); a room token never can.
// The caller's issuer name: its paired grant, or its KYPP_API_TOKENS name when that is listed in
// KYPP_ROOM_ISSUERS. Only a configured issuer may link rooms to repos.
async function roomIssuer(request: Request, env: Env): Promise<{ name: string; grant?: string; credential?: string; projects?: string[] } | null> {
  const token = request.headers.get("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!token) return null;
  const paired = await pairedGrant(env.DB, token);
  if (paired) return paired;
  const t = await apiToken(token, env.KYPP_API_TOKENS);
  const credential = await digest(token);
  return t && (await configuredIssuers(env)).get(t.name)?.has(credential) ? { name: t.name, credential, ...(t.projects ? { projects: t.projects } : {}) } : null;
}

// KYPP_ROOM_ISSUERS names that still have a token, each with its secrets' digests. Removing either
// one pauses their rooms; a seat stops working once the secret that made it is gone.
const configuredIssuers = async (env: Env) => {
  const listed = new Set(owners(env.KYPP_ROOM_ISSUERS));
  const issuers = new Map<string, Set<string>>();
  for (const [secret, { name }] of apiTokens(env.KYPP_API_TOKENS)) {
    if (listed.has(name)) issuers.set(name, (issuers.get(name) ?? new Set()).add(await digest(secret)));
  }
  return issuers;
};

// The owner check for kypp's own pages, behind the passphrase rate limit.
function pairingDeps(request: Request, env: Env, base: string): PairingDeps {
  return {
    base,
    checkOwner: async (secret) => {
      if (!(await allowAttempt(request, env))) return "limited";
      return env.KYPP_OWNER_SECRET && (await sameSecret(secret, env.KYPP_OWNER_SECRET)) ? "ok" : "wrong";
    },
  };
}

// The public origin: the OAuth issuer, the MCP resource id (<base>/mcp) and the OIDC audience.
// KYPP_PUBLIC_URL pins it; a Deploy to Cloudflare install leaves it unset and gets the request's origin.
export function publicBase(env: Pick<Env, "KYPP_PUBLIC_URL">, request: Request): string {
  return (env.KYPP_PUBLIC_URL || new URL(request.url).origin).replace(/\/+$/, "");
}

// /v1/systemone callers: a GitHub Actions OIDC token for <base> from a listed owner, or
// a KYPP_S1_TOKENS token for callers outside Actions. Returns a label for logs, never the token.
async function systemOneCaller(token: string, env: Env, base: string): Promise<string | null> {
  if (looksLikeJwt(token)) {
    const c = await oidc.verify(token, { audience: base, owners: owners(env.KYPP_OIDC_OWNERS) });
    if (c) return `oidc ${c.repository} ${c.workflow}`;
  }
  const name = await tokenName(token, env.KYPP_S1_TOKENS);
  return name ? `token ${name}` : null;
}

// One provider per public origin, so a Worker reached on two hostnames keeps each one's issuer apart.
const providers = new Map<string, OAuthProvider<Env>>();
function oauthProvider(base: string): OAuthProvider<Env> {
  let provider = providers.get(base);
  if (provider) return provider;
  provider = new OAuthProvider<Env>({
    apiRoute: "/mcp",
    apiHandler: {
      fetch: (request: Request, env: Env, ctx: ExecutionContext) => handleMcp(request, env.DB, (ctx as any).props as Caller,
        { s1: s1Client(env), policy: policyFrom(env), sessionKey: `${env.CONSENT_SECRET}:session`, correct: env.KYPP_CORRECT !== "off" }),
    },
    defaultHandler: {
      fetch: (request: Request, env: Env) => {
        const path = new URL(request.url).pathname;
        if (path === "/authorize") return authorize(request, env, base);
        if (path === "/") return new Response("kypp memory server. Add <this URL>/mcp to your coding agent as a remote MCP server.\n");
        return new Response("not found", { status: 404 });
      },
    },
    authorizeEndpoint: "/authorize",
    tokenEndpoint: "/oauth/token",
    clientRegistrationEndpoint: "/oauth/register",
    scopesSupported: ["mcp", "offline_access"],
    resourceMetadata: { resource: `${base}/mcp`, authorization_servers: [base], resource_name: "kypp memory" },
    clientIdMetadataDocumentEnabled: true,
    resolveExternalToken: async ({ token, env }) => {
      const t = await apiToken(token, env.KYPP_API_TOKENS);
      if (t) return { props: { user: t.name, agent: t.name, ...(t.projects ? { projects: t.projects } : {}) } satisfies Caller, audience: `${base}/mcp` };
      const room = await roomCaller(env.DB, token, await configuredIssuers(env));
      return room ? { props: room, audience: `${base}/mcp` } : null as any;
    },
  });
  providers.set(base, provider);
  return provider;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    // Registration is open by design (MCP clients self-register), so throttle it before it writes to KV.
    const path = new URL(request.url).pathname;
    if (request.method === "POST" && path === "/oauth/register"
      && !(await allowAttempt(request, env, env.REGISTER_LIMIT, "register"))) return tooMany();
    if (request.method === "POST" && path === PAIR_PATH
      && !(await allowAttempt(request, env, env.REGISTER_LIMIT, "pair"))) return tooMany();
    // TypeSafe-compatible System One for headless tools (brief triage in CI). GitHub Actions OIDC or
    // its own tokens, never KYPP_API_TOKENS or OAuth, so a CI caller can reach the model but not memory.
    const base = publicBase(env, request);
    if (path === SYSTEM_ONE_PATH) return handleSystemOne(request, env.AI, (token) => systemOneCaller(token, env, base));
    // The operator's CLI (KYPP_URL): its own secret, never OAuth or KYPP_API_TOKENS, and never on /mcp.
    if (path === OPERATOR_PATH) return handleOperator(request, env.DB, env.KYPP_OPERATOR_TOKEN, { s1: s1Client(env), policy: policyFrom(env) });
    if (path === PAIR_PATH || path.startsWith(`${PAIR_PATH}/`)) return handlePair(request, env.DB, pairingDeps(request, env, base));
    if (path === PAIRS_PATH) return handlePairs(request, env.DB, pairingDeps(request, env, base));
    if (path === ROOMS_PATH || path.startsWith(`${ROOMS_PATH}/`)) {
      const issuer = await roomIssuer(request, env);
      return issuer ? handleRooms(request, env.DB, issuer.name, issuer.grant === undefined ? { canLink: true, credential: issuer.credential, ...(issuer.projects ? { linkable: issuer.projects } : {}) } : { canLink: false, grant: issuer.grant }) : new Response("unauthorized", { status: 401 });
    }
    return oauthProvider(base).fetch(request, env, ctx);
  },

  // The defrag pass: file unsorted claims, supersede duplicates and promote subjects that recur
  // across sessions. Groups that changed while it ran are skipped and picked up next hour.
  async scheduled(_event: ScheduledController, env: Env) {
    await defrag(new D1Store(env.DB), policyFrom(env), s1Client(env), tokenPins(env.KYPP_API_TOKENS));
  },
};
