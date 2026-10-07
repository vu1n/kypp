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
- **Placement (origin + defrag):** a claim records the repo name it was written from. Agents only
  choose `scope=user` (how this person works); they never pick global. A write with no project, or
  one not in `KYPP_PROJECTS`, still lands as *unsorted*: the writing session sees it right away,
  other sessions only once it is filed. The gate tries to file it into a known project in the same
  call, and the hourly defrag pass retries (an origin that has since joined `KYPP_PROJECTS` files
  without the model). Every filing is logged in the claim's `metadata.filed`. Leave
  `KYPP_PROJECTS` unset to keep treating any well-formed name as a project. Recall and briefing
  lines say where each came from: `(repo)`, `(user)`, `(global)` or `(unsorted)`.
- **Automatic, with a judge:** promotion needs no person in any scope. With the gate on, the
  defrag pass first asks the model whether the supporting claims agree; if they look conflicted,
  the subject waits as candidates until a later pass. `correct` stays trust-based: any signed-in
  agent can land an accepted project-scope answer, so `KYPP_CORRECT=off` (the shipped default in
  `wrangler.jsonc`) hides it from the tool list and refuses calls.
- **Write gate (Clef, or Jev):** each `claim` is scored first by Clef on the Worker's Workers AI
  binding (`KYPP_S1_MODEL=clef-flash` for the faster one), or by TypeSafe when `TYPESAFE_API_KEY` is set. Status and
  session detail are turned away, a defaulted type is relabelled when the model is sure, and how
  general the lesson looks is recorded. The model may file an unsorted claim into one known
  project when sure; it never accepts or widens a claim. `KYPP_S1=off`, a 5-second timeout or an error all mean no gate. Each claim's subject and
  content go to Workers AI in your Cloudflare account, or to the TypeSafe API when a key is set.
- **Sessions:** each MCP session gets an id at `initialize`; claims are stamped with it and usage is
  logged per session. A subject two sessions claim is promoted by the hourly defrag pass.
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
npm run dev:local -- --test-scheduled        # in one shell; drops the AI binding, which needs a Cloudflare login
npm test && node test/smoke.mjs              # unit + store tests (node:sqlite), then the end-to-end smoke (mocks TypeSafe on :8788)
npm run typecheck
```
