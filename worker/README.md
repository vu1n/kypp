# kypp Worker — hosted memory over remote MCP

The same memory as local kypp, reachable from cloud sessions: a Cloudflare Worker over D1, served as
a remote MCP server with OAuth. Local kypp is unchanged; this is the optional hosted store.

- **Tools:** `briefing`, `recall`, `claim`, `expand`, `correct` (same contract as `kypp serve`).
- **Placement:** an agent says which repo it is in and never picks how widely a lesson is shared.
  A claim records that origin for good and is filed into the matching row of the `projects` table.
  A missing or unknown name (say "dev") is not an error and never creates a project: the claim
  lands *unsorted*, visible only to the session that wrote it, until it is filed. Filing is tried
  at write time and again by the hourly defrag pass, by name if the project has since been
  registered, else by a confident System One choice among the registered projects. Every move is
  written to `defrag_log`. The agent's one choice is `scope: "user"` for how the signed-in person
  works, in every repo; `global` is not agent-writable.
- **Reads** cover the caller's project, their user scope and global. Each line carries its level
  (`@kypp`, `@user`, `@global`, `@unsorted`), and a call with no known project says so instead of
  quietly returning less. The writing agent is a label you can filter recall by (`agent`).
- **Projects** are rows you add; `description` is what the filing model reads to tell them apart:
  `npx wrangler d1 execute kypp --remote --command "INSERT INTO projects(name, description, created_at) VALUES ('myrepo', 'one line on what it is', datetime('now'))"`.
- **Categories:** the claim types (pitfall, decision, procedure, preference, fact, artifact,
  hypothesis). The briefing is grouped by type.
- **Promotion:** a claim is accepted once it recurs in two sessions at least
  `KYPP_RECUR_GAP_MINUTES` (default 60) apart. That measures repetition, not independent evidence:
  one client can open two sessions, so the gap just makes faking it slow. Session ids are signed,
  so a client can't invent one. A newer claim on an accepted subject waits as a pending update and
  replaces the old answer once it recurs.
- **Automatic, with a judge:** promotion needs no person in any scope. With the gate on, the
  cleanup pass first asks the model whether the supporting claims agree; if they look conflicted,
  the subject waits as candidates until a later pass. `correct` stays trust-based: any signed-in
  agent can land an accepted project-scope answer, so `KYPP_CORRECT=off` (the shipped default in
  `wrangler.jsonc`) hides it from the tool list and refuses calls.
- **Write gate (Clef, or Jev):** each `claim` is scored first by Clef on the Worker's Workers AI
  binding (`KYPP_S1_MODEL=clef-flash` for the faster one), or by TypeSafe when `TYPESAFE_API_KEY` is set. Status and
  session detail are turned away, a defaulted type is relabelled when the model is sure, and how
  general the lesson looks is recorded. The model never accepts or widens a claim; filing an
  unsorted claim is its only placement. `KYPP_S1=off`, a 5-second timeout or an error all mean no gate. Each claim's subject and
  content go to Workers AI in your Cloudflare account, or to the TypeSafe API when a key is set.
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
# register each repo that will use it (see Projects above); until then every claim lands unsorted
npx wrangler deploy                          # prints https://kypp.<subdomain>.workers.dev
# set vars.KYPP_PUBLIC_URL in wrangler.jsonc to that origin (OAuth issuer + resource id), then:
npx wrangler secret put KYPP_OWNER_SECRET    # the passphrase you type on the sign-in page; make it long
npx wrangler secret put CONSENT_SECRET       # 32+ random chars
npx wrangler secret put KYPP_API_TOKENS      # optional: "huddles:<long random token>" for headless clients
npx wrangler secret put TYPESAFE_API_KEY     # optional: route the gate to TypeSafe instead of Clef on Workers AI
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
npx wrangler d1 execute kypp --local --command "INSERT OR IGNORE INTO projects(name, created_at) VALUES ('smoke', datetime('now'))"
npm run dev:local -- --test-scheduled        # in one shell; drops the AI binding, which needs a Cloudflare login
npm test && node test/smoke.mjs              # unit + store tests (node:sqlite), then the end-to-end smoke (mocks TypeSafe on :8788)
npm run typecheck
```
