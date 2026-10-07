# kypp Worker — hosted memory over remote MCP

The same memory as local kypp, reachable from cloud sessions: a Cloudflare Worker over D1, served as
a remote MCP server with OAuth. Local kypp is unchanged; this is the optional hosted store.

- **Tools:** `briefing`, `recall`, `claim`, `expand`, `correct` (same contract as `kypp serve`).
- **Scopes:** `project` (this repo), `user` (the signed-in person, in every repo) and
  `global` (everyone). Recall and briefing read all three, nearest first. The writing agent is a
  label you can filter recall by (`agent`), not a scope.
- **Categories:** the claim types (pitfall, decision, procedure, preference, fact, artifact,
  hypothesis). The briefing is grouped by type.
- **Promotion:** a claim is accepted once it recurs in two sessions at least
  `KYPP_RECUR_GAP_MINUTES` (default 60) apart. That measures repetition, not independent evidence:
  one client can open two sessions, so the gap just makes faking it slow. Session ids are signed,
  so a client can't invent one. A newer claim on an accepted subject waits as a pending update and
  replaces the old answer once it recurs.
- **Automatic, with a judge:** promotion needs no person in any scope. With a TypeSafe key, the
  cleanup pass first asks the model whether the supporting claims agree; if they look conflicted,
  the subject waits as candidates until a later pass. `correct` stays trust-based: any signed-in
  agent can land an accepted project-scope answer, so `KYPP_CORRECT=off` (the shipped default in
  `wrangler.jsonc`) hides it from the tool list and refuses calls.
- **Optional write gate (Jev, Clef):** with a TypeSafe key, each `claim` is scored first. Status and
  session detail are turned away, a defaulted type is relabelled when the model is sure, and how
  general the lesson looks is recorded. The model never accepts a claim or moves it between
  scopes. No key, a 5-second timeout or an error all mean no gate. With a key, each claim's subject
  and content are sent to the TypeSafe API (or wherever `TYPESAFE_BASE_URL` points).
- **Sessions:** each MCP session gets an id at `initialize`; claims are stamped with it and usage is
  logged per session. A subject two sessions claim is promoted by the hourly cleanup pass.
- **Authority:** the Worker only writes agent authority. Human authority stays with the local
  operator's `kypp correct`.
- **No Durable Objects.** The MCP endpoint is stateless JSON over HTTP.

## Deploy

```sh
cd worker && npm install
npx wrangler login
npx wrangler d1 create kypp                  # paste database_id into wrangler.jsonc
npx wrangler kv namespace create OAUTH_KV    # paste id into wrangler.jsonc
npx wrangler d1 migrations apply kypp --remote
npx wrangler deploy                          # prints https://kypp.<subdomain>.workers.dev
# set vars.KYPP_PUBLIC_URL in wrangler.jsonc to that origin (OAuth issuer + resource id), then:
npx wrangler secret put KYPP_OWNER_SECRET    # the passphrase you type on the sign-in page; make it long
npx wrangler secret put CONSENT_SECRET       # 32+ random chars
npx wrangler secret put KYPP_API_TOKENS      # optional: "huddles:<long random token>" for headless clients
npx wrangler secret put TYPESAFE_API_KEY     # optional write gate; set TYPESAFE_BASE_URL / TYPESAFE_DEFAULT_MODEL vars for Clef
npx wrangler deploy
```

## Connect

- **Claude Code:** in each repo, `claude mcp add --transport http --scope project kypp https://<worker>/mcp --header "X-Kypp-Project: <repo>"`,
  then `/mcp` to sign in. `--scope project` writes `.mcp.json` into the repo, so the project
  header is pinned per repo. One Worker holds every repo's memory; a call with no project can
  read only user and global claims and can't write project ones.
- **Claude (projects and chat):** Settings → Connectors → Add custom connector → `https://<worker>/mcp`.
- **Headless (huddles):** `Authorization: Bearer <token from KYPP_API_TOKENS>`.

## Develop

```sh
cp .dev.vars.example .dev.vars
npx wrangler d1 migrations apply kypp --local
npx wrangler dev --test-scheduled            # in one shell
npm test && node test/smoke.mjs              # unit + store tests (node:sqlite), then the end-to-end smoke (mocks TypeSafe on :8788)
npm run typecheck
```
