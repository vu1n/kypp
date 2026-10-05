// index.ts — kypp's hosted store: one Worker that is both the OAuth authorization server and the MCP
// resource at /mcp, over D1. Sign-in is a single owner passphrase (KYPP_OWNER_SECRET); headless
// clients can present a named static token from KYPP_API_TOKENS instead.
import { AuthorizationError, OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { type Caller, handleMcp } from "./mcp.ts";
import { planConsolidation } from "./memory.ts";
import { type S1Env, s1Client } from "./s1.ts";
import { D1Store } from "./store.ts";

export interface Env extends S1Env {
  DB: D1Database;
  OAUTH_KV: KVNamespace;
  OAUTH_PROVIDER: any; // injected by OAuthProvider for the default handler
  KYPP_PUBLIC_URL: string; // e.g. https://kypp.<account>.workers.dev
  KYPP_OWNER_SECRET: string;
  CONSENT_SECRET: string;
  KYPP_API_TOKENS?: string;
}

const enc = new TextEncoder();
const escape = (v: string) => v.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);

async function sameSecret(a: string, b: string): Promise<boolean> {
  // Hash both sides first so lengths match for the constant-time compare.
  const [ha, hb] = await Promise.all([a, b].map((v) => crypto.subtle.digest("SHA-256", enc.encode(v))));
  return crypto.subtle.timingSafeEqual(ha, hb);
}

function consentPage(d: { clientName: string; clientDomain?: string; redirectHost: string; redirectIsLoopback: boolean }, handle: string, error = ""): string {
  const name = escape(d.clientName);
  return `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>kypp sign-in</title>
<style>body{font:16px system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem}input{font:inherit;padding:.4rem;width:100%}button{font:inherit;padding:.4rem 1rem;margin-right:.5rem}.err{color:#b00}</style>
<h1>Allow ${name} to use your kypp memory?</h1>
<p>${d.clientDomain ? `Published by <strong>${escape(d.clientDomain)}</strong>.` : "This app registered itself; its name is not verified."}
Access goes to <strong>${escape(d.redirectHost)}</strong>.</p>
${d.redirectIsLoopback ? "<p><strong>This sends access to an app on your computer.</strong> Continue only if you just started signing in from it.</p>" : ""}
${error ? `<p class="err">${escape(error)}</p>` : ""}
<form method="post">
<input type="hidden" name="handle" value="${escape(handle)}">
<p><label>Owner passphrase<br><input type="password" name="secret" autocomplete="current-password" required></label></p>
<p><button name="decision" value="approve">Allow</button><button name="decision" value="deny" formnovalidate>Deny</button></p>
</form>`;
}

const html = (body: string, headers: Headers, status = 200) => {
  headers.set("Content-Type", "text/html; charset=utf-8");
  return new Response(body, { status, headers });
};

async function authorize(request: Request, env: Env): Promise<Response> {
  const oauth = env.OAUTH_PROVIDER;
  try {
    if (request.method === "GET") {
      const req = await oauth.parseAuthRequest(request);
      const details = await oauth.describeConsent(req);
      const consent = await oauth.beginConsent(req);
      return html(consentPage(details, consent.handle), consent.headers);
    }
    const form = await request.formData();
    const handle = String(form.get("handle"));
    if (form.get("decision") !== "approve") {
      const denied = await oauth.denyConsent(request, handle);
      return new Response(null, { status: 302, headers: denied.headers });
    }
    if (!env.KYPP_OWNER_SECRET || !(await sameSecret(String(form.get("secret") ?? ""), env.KYPP_OWNER_SECRET))) {
      // The handle is single-use only once approved/denied, so the same page can be retried.
      return new Response("Wrong passphrase. Go back and try again.", { status: 403, headers: { "Content-Type": "text/plain; charset=utf-8" } });
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
    if (error instanceof AuthorizationError) return new Response(escape((error as any).description ?? "authorization failed"), { status: 400 });
    throw error;
  }
}

// Static tokens for clients that can't do the browser flow. "name:token,name:token".
function apiTokens(env: Env): Map<string, string> {
  const m = new Map<string, string>();
  for (const pair of (env.KYPP_API_TOKENS ?? "").split(",")) {
    const i = pair.indexOf(":");
    if (i > 0 && pair.length - i > 16) m.set(pair.slice(i + 1).trim(), pair.slice(0, i).trim());
  }
  return m;
}

let provider: OAuthProvider<Env> | null = null;
function oauthProvider(env: Env): OAuthProvider<Env> {
  const base = env.KYPP_PUBLIC_URL.replace(/\/+$/, "");
  provider ??= new OAuthProvider<Env>({
    apiRoute: "/mcp",
    apiHandler: {
      fetch: (request: Request, env: Env, ctx: ExecutionContext) => handleMcp(request, env.DB, (ctx as any).props as Caller, s1Client(env)),
    },
    defaultHandler: {
      fetch: (request: Request, env: Env) => {
        const path = new URL(request.url).pathname;
        if (path === "/authorize") return authorize(request, env);
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
      for (const [secret, name] of apiTokens(env)) {
        if (await sameSecret(token, secret)) return { props: { user: name, agent: name } satisfies Caller, audience: `${base}/mcp` };
      }
      return null as any;
    },
  });
  return provider;
}

export default {
  fetch(request: Request, env: Env, ctx: ExecutionContext) {
    return oauthProvider(env).fetch(request, env, ctx);
  },

  // The cleanup pass: supersede duplicates and promote subjects two sessions agree on. It is the
  // only writer that changes existing rows, so it runs in one place on a schedule.
  async scheduled(_event: ScheduledController, env: Env) {
    const store = new D1Store(env.DB);
    await store.apply(planConsolidation(await store.liveClaims()));
  },
};
