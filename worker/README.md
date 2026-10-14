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
- **Reads** cascade from the caller's project through the groups it rolls up to, then their user
  scope and global, nearest first on a relevance tie. Each line carries its level (`@kypp`,
  `@group:tools`, `@user`, `@global`, `@unsorted`), and a call with no known project says so instead
  of quietly returning less. The writing agent is a label you can filter recall by (`agent`).
- **Multiplayer:** recall also shows other sessions' unconfirmed (`?`) claims in the caller's own
  project, ranked below settled ones; the briefing lists the newest few apart, under "recent from
  other agents". A session that was shown another's candidate and then claims the same subject is
  recorded as an echo (`metadata.echo_of`, no source session), so repeating what you read never
  counts toward accepting it. The write gate also holds back text that reads as instructions aimed
  at other agents (override instructions, send credentials somewhere) rather than a lesson; a claim
  written while the model was down is judged by the next defrag pass instead.
- **Orgs** are groups at the top of the roll-up (no parent of their own), such as the GitHub owner
  above all its repos. The briefing lists an org's accepted claims first, under "how this org
  works". Agents never write there (memory-scope-decay §9).
- **Groups** are rows in `project_parents` (a project and a group it belongs to; a group can have
  parents too, up to three levels). Only defrag places claims at a group; agents never write there:
  `npx wrangler d1 execute kypp --remote --command "INSERT INTO project_parents(project, parent, created_at) VALUES ('kypp', 'tools', datetime('now'))"`.
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
- **System One for other tools:** `POST /v1/systemone` speaks the TypeSafe API's System One
  route and answers with Clef on the same `AI` binding, so a TypeSafe client works unchanged against
  this Worker. `brief triage` in CI sets `TYPESAFE_BASE_URL=https://<worker>` and
  `TYPESAFE_DEFAULT_MODEL=clef` (or `clef-flash`; other models get 400), and sends a GitHub Actions
  OIDC token as `TYPESAFE_API_KEY`, so the calling repo needs no secret. The Worker checks the
  token's RS256 signature against GitHub's JWKS (cached, refetched on an unknown `kid`), `iss`
  (`https://token.actions.githubusercontent.com`), `aud` (exactly `KYPP_PUBLIC_URL` with no trailing
  slash), `exp`/`nbf` (60 s skew) and that `repository_owner` is listed in `KYPP_OIDC_OWNERS`
  (comma list; unset or empty refuses every OIDC token). Callers outside Actions can still present
  a static token from `KYPP_S1_TOKENS`. Anything else is 401; no `AI` binding means 503. A model
  failure logs the caller (repository and workflow, or the static token's name), never the token.
  It is a pure proxy and never touches the claims store.
- **Sessions:** each MCP session gets an id at `initialize`; claims are stamped with it and usage is
  logged per session. A subject two sessions claim is promoted by the hourly cleanup pass.
- **Authority:** the Worker only writes agent authority. Human authority stays with the local
  operator's `kypp correct` (authority-order@0004 also allows a ratified-decision `POST /sync`, not
  built yet).
- **Rooms** (memory-scope-decay §11): a chat room is its own principal with a private lane, shared
  by every bot the integration seats there. An integration whose `KYPP_API_TOKENS` name is listed in
  `KYPP_ROOM_ISSUERS` seats a bot with `POST /rooms {"id": "<room>", "member": "<bot>", "links":
  ["<registered project>", ...]}` (Bearer: its API token) and gets that member's `kypr_` token once;
  kypp keeps only its digest. The room is registered on first use. Seating the same member again
  rotates its token; `links`, when given, replaces the room's links. `DELETE
  /rooms/<id>/members/<member>` removes one bot; `DELETE /rooms/<id>` closes the room for good. On
  `/mcp` a member token pins everything: the bot reads its room's lane plus its linked projects'
  settled memory (and their groups), never user scope, global or the project list, and its claims
  stay in the lane. A claim leaves only with `share_to` naming a linked project; it lands there as a
  candidate marked `shared_from`. Defrag promotes inside a lane and never moves a claim out of it.
- **Pairing** (memory-scope-decay §11.7): an app can earn the right to register rooms without any
  operator config. It calls `POST /pair {"name": "<who is asking>"}` and gets a secret `handle`, a
  `user_code` like `KQ7M-4TZ2` and a `verification_uri`. It shows the code and link, and polls `POST
  /pair/token {"handle"}` every `interval` seconds. Until the owner approves, the poll answers
  `authorization_pending` (RFC 8628 style). The owner opens the link, checks the code and approves
  with the passphrase. The next poll returns a `kypi_` issuer token once. That token only calls
  `/rooms` for its own namespace, and can't set links. `DELETE /pair` with it unlinks the app: every
  token under it stops working, but its rooms and their memory are kept. When approving a later
  pairing, the owner can choose to continue an unlinked app; the new token takes over its namespace
  (`"resumed": true` in the redeem answer) and its rooms and bots work again. Codes expire in 10
  minutes. The owner can see and unlink paired apps at `/pairs`.
- **No Durable Objects.** The MCP endpoint is stateless JSON over HTTP.

## Deploy

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/vu1n/kypp/tree/main/worker)

The button needs no Cloudflare API token. It copies this directory into a new repo on your GitHub,
creates the D1 database and KV namespace, asks for the two secrets in `.dev.vars.example`
(`KYPP_OWNER_SECRET`, the sign-in passphrase, and `CONSENT_SECRET`, 32+ random characters), then
builds with `npm run deploy`, which applies the D1 migrations before `wrangler deploy`. Every push to
the new repo redeploys. After it finishes:

1. Register each repo that will use it (see Projects above); until then every claim lands unsorted.
   The Cloudflare dashboard's D1 console runs the same `INSERT` without a CLI.
2. Connect your agents (below). The Worker uses the origin it is reached on as its OAuth issuer;
   set `KYPP_PUBLIC_URL` on the Worker only to pin one, for example a custom domain.

Optional settings are Worker variables and secrets, set in the dashboard or with Wrangler. A deploy
keeps variables set outside `wrangler.jsonc` (`keep_vars`), so they survive redeploys:

- `KYPP_OIDC_OWNERS` (variable, e.g. `vu1n`): GitHub owners whose Actions may call `/v1/systemone`.
  Unset refuses every OIDC token.
- `KYPP_API_TOKENS` (secret, `huddles:<long random token>`): static tokens for headless clients.
- `KYPP_ROOM_ISSUERS` (variable, e.g. `huddles`): `KYPP_API_TOKENS` names that may register chat
  rooms (see Rooms). Unset refuses every registration.
- `KYPP_S1_TOKENS` (secret, `name:<long random token>`): `/v1/systemone` callers outside Actions.
- `TYPESAFE_API_KEY` (secret): route the gate to TypeSafe instead of Clef on Workers AI.

From a checkout instead of the button:

```sh
cd worker && npm install
npx wrangler login
npx wrangler deploy                          # first time: creates the D1 database and KV namespace
npx wrangler secret put KYPP_OWNER_SECRET    # the passphrase you type on the sign-in page; make it long
npx wrangler secret put CONSENT_SECRET       # 32+ random chars
npm run deploy                               # applies migrations, then deploys; use this from now on
```

## Connect

- **Claude Code:** in each repo, `claude mcp add --transport http --scope project kypp https://<worker>/mcp --header "X-Kypp-Project: <repo>"`,
  then `/mcp` to sign in. `--scope project` writes `.mcp.json` into the repo, so the project
  header is pinned per repo. One Worker holds every repo's memory; a call with no known project
  reads only user and global claims, and its claims land unsorted (see Placement).
- **Claude (projects and chat):** Settings → Connectors → Add custom connector → `https://<worker>/mcp`.
- **Headless:** `Authorization: Bearer <token from KYPP_API_TOKENS>`.
- **Chat-room bots (Huddles):** register the room (see Rooms) and give the bot its `kypr_` token as
  `Authorization: Bearer`.

## Develop

```sh
cp smoke.dev.vars .dev.vars
npx wrangler d1 migrations apply kypp --local
npx wrangler d1 execute kypp --local --command "INSERT OR IGNORE INTO projects(name, created_at) VALUES ('smoke', datetime('now'))"
npm run dev:local -- --test-scheduled        # in one shell; drops the AI binding, which needs a Cloudflare login
npm test && node test/smoke.mjs              # unit + store tests (node:sqlite), then the end-to-end smoke (mocks TypeSafe on :8788)
npm run typecheck
```
