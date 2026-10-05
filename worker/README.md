# kypp Worker — hosted memory over remote MCP

The same memory as local kypp, reachable from cloud sessions: a Cloudflare Worker over D1, served as
a remote MCP server with OAuth. Local kypp is unchanged; this is the optional hosted store.

- **Tools:** `briefing`, `recall`, `claim`, `expand`, `correct` (same contract as `kypp serve`).
- **Sessions:** each MCP session gets an id at `initialize`; claims are stamped with it and usage is
  logged per session. A subject two sessions claim is promoted by the hourly cleanup pass.
- **Authority:** the Worker only writes agent authority. Human authority stays with the local
  operator's `kypp correct`.
- **No Durable Objects.** The MCP endpoint is stateless JSON over HTTP.

## Deploy

```sh
cd worker && npm install
npx wrangler d1 create kypp                  # paste database_id into wrangler.jsonc
npx wrangler kv namespace create OAUTH_KV    # paste id into wrangler.jsonc
# set vars.KYPP_PUBLIC_URL in wrangler.jsonc to the Worker's https origin
npx wrangler d1 migrations apply kypp --remote
npx wrangler secret put KYPP_OWNER_SECRET    # the passphrase you type on the sign-in page; make it long
npx wrangler secret put CONSENT_SECRET       # 32+ random chars
npx wrangler secret put KYPP_API_TOKENS      # optional: "huddles:<long random token>" for headless clients
npx wrangler deploy
```

## Connect

- **Claude Code:** `claude mcp add --transport http kypp https://<worker>/mcp`, then `/mcp` to sign in.
  Add `--header "X-Kypp-Project: <repo>"` to skip passing `project` on every call.
- **Claude (projects and chat):** Settings → Connectors → Add custom connector → `https://<worker>/mcp`.
- **Headless (huddles):** `Authorization: Bearer <token from KYPP_API_TOKENS>`.

## Develop

```sh
cp .dev.vars.example .dev.vars
npx wrangler d1 migrations apply kypp --local
npx wrangler dev --test-scheduled            # in one shell
npm test && node test/smoke.mjs              # unit tests, then the end-to-end smoke
npm run typecheck
```
