---
id: hosted-worker
paths:
  - "worker/**"
entry:
  - "worker/src/index.ts"
  - "worker/src/memory.ts"
tests:
  - "worker/test/memory.test.ts"
  - "worker/test/mcp-contract.test.ts"
  - "worker/test/operator.test.ts"
  - "worker/test/defrag.test.ts"
  - "worker/test/scenarios.test.ts"
---
# Hosted Worker

The optional hosted store: a Cloudflare Worker over D1 serving `briefing`, `recall`, `claim`,
`expand` and `correct` as a remote MCP server behind OAuth, so cloud sessions share one memory.
`worker/src/memory.ts` ports the arbiter's ranking and promotion rules; an hourly cron runs the
defrag pass (`worker/src/defrag.ts`: file unsorted claims, then consolidate). It adds a `user` scope (the signed-in person, every repo) beside `project` and
`global`, and an optional System One write gate (`worker/src/s1.ts`, TypeSafe JS SDK).
`worker/src/systemone.ts` serves the same Clef model to other tools at `POST /v1/systemone` in the
TypeSafe API's shape, for GitHub Actions OIDC tokens (`worker/src/oidc.ts`) and static `KYPP_S1_TOKENS`.

## Gotchas
- `memory.ts` mirrors `arbiter.py` and `view.py` by hand. A ranking or promotion change lands in
  both, or local and hosted memory disagree.
- `ftsQuery` splits on Unicode letters, marks, digits and `_` so a query word reaches `unicode61`
  whole and gets the same case and accent folding as the index. An ASCII-only split turned `café`
  into `caf` and `日本語` into an empty match. A recall query with text but no words returns
  nothing, never browse results. Recall order ends in `c.id`, so ties don't depend on the engine.
  `worker/bench/replay` replays one query set against every store to check this.
- `POST /v1/operator` (`worker/src/operator.ts`, `kypp/remote.py`) is the only hosted writer of human
  authority, `verify` commands and verified results, behind `KYPP_OPERATOR_TOKEN`. Keep
  `D1Store.operatorClaim`, `markVerified` and `operatorReject` off every MCP path, and log each in
  `defrag_log`. `markVerified` mirrors `MemoryStore.mark_verified`: change one, change both.
- Hosted operator `correct` is an explicit replacement in the existing `memory_claims` store,
  not a rank contest. Insert, FTS/pointers, exact-group supersession and audit share one D1 batch;
  the returned superseded count comes from that transaction. Equal clocks, query order and lower
  confidence must not discard the new correction. This does not activate the staged adapters.
- `D1Store.recall` quotes Unicode word tokens before FTS5 `MATCH`. Binding a raw path throws
  `syntax error near "/"`, and the pointer rank never runs. MCP passes the raw query;
  recall itself turns it into tokens and matches the path, anchor, or doc:// ref on
  `code_pointers` first.
- `wrangler.jsonc`'s top level is the Deploy to Cloudflare template; the owner's install is
  `env.production`, which repeats every binding and var because envs don't inherit them. Change one,
  change both. Deploy the install only with `npm run deploy:production`: a plain `wrangler deploy`
  targets the template and turns workers.dev back on.
- The OAuth resource id is exactly `<KYPP_PUBLIC_URL>/mcp`. A path or query suffix on the MCP URL
  breaks token audience checks, so the project travels as a tool argument or the
  `X-Kypp-Project` header.
- The MCP endpoint is stateless: the session id from `initialize` is only echoed back, never
  stored. Don't add a Durable Object to hold sessions.
- The `user` scope exists only in the Worker; local `vocab.Scope` is still project/global. On the
  user scope the `user` column is the owner, so a static token's name decides whose user scope it reads.
- The `room` scope is Worker-only too, and also keys on `user` (as
  `room:<integration>:<id>`). Any query that guards `scope != 'user'` must guard `room` as well, or one
  room's lane leaks to other callers through that path; `roomVisible` and `get(…, room)` are the only
  reads a room token gets.
- The S1 gate runs Clef on the `AI` binding unless `TYPESAFE_API_KEY` is set. Workers AI is always
  remote, so plain `wrangler dev` fails without a Cloudflare login; `npm run dev:local` (and CI's
  smoke) strips the binding and uses the TypeSafe mock from `.dev.vars`.
- The S1 model fails open. It filters and labels writes and files unsorted claims into a registered
  project; it must never accept or widen a claim.
- A project exists only as a row in `projects`. A name from a header or argument is resolved
  against it (`resolveOrigin`) and never inserted, so tests and the smoke run must register theirs.
  A project-scope claim with `project IS NULL` is unsorted: only its own session reads it, and
  consolidation skips it (`liveClaims({ filed: true })`) so it can't be promoted before it is filed.
- A `group` claim stores the group's name in `project`, so any query keyed on `project` alone must
  also check `scope`. Groups come from `project_parents` (`store.ancestors`, recursive, depth < 4,
  cycles stop); nothing writes `scope='group'` until step 2 widening, so group reads are empty until then.
- Every HTML page renders through `src/page.ts` (`render`, `htmlResponse`): one stylesheet
  (`worker/DESIGN.md`), `no-store`, and no framing. A refused consent (wrong passphrase, rate limit)
  re-parses the request from the POST's `/authorize?…` query and begins a fresh consent handle, so
  the page always stands for what it shows; a POST without that query gets the error page alone,
  with the same status. Don't round-trip consent details through hidden fields.
- Recall shows other sessions' candidates in the caller's project by default (`candidates: "origin"`).
  The echo guard (`store.shownCandidate`) relies on `claim_usages` rows for what a session was shown,
  so every read path that renders claims must keep calling `recordUsage`.
- The briefing lists accepted claims at the caller's org (a group with no parent of its own) first,
  under "how this org works", and leaves nearer groups in the usual per-type sections.
- The briefing also lists the user's own unconfirmed `user`-scope claims from their other sessions
  (process and preferences), in every repo. A process lesson is often learned once per repo, so
  waiting for acceptance hid it. The echo guard covers user scope too: re-claiming a lesson the
  briefing showed doesn't count toward accepting it.
- A `KYPP_API_TOKENS` entry `name@projA|projB:token` pins that token (`Caller.projects`). `callTool`
  sends pinned callers to `pinnedTool`, which never shows the project list; defrag finds a pinned
  token's claims by `user = agent = name` (`tokenPins`). A new read path, write path or filer must
  honour the pin too, or the token reaches every registered project.
- `DROP_BELOW` was tuned on Clef with `eval/write_gate` (rerun its README's two commands). A new
  gate model or a reworded keep question needs a re-tune, not a copied number: Clef scores junk
  well above its face value, and a cutoff fitted to one model's scale misplaces another's. Until a
  model is tuned, `dropBelowFor` keeps it on the old 0.1 and warns.
- `recall` with a query and an S1 model searches `RERANK_POOL` (40) keyword hits, scores each in its
  own request (`s1.relevance`), drops those under `RELEVANT_FLOOR` and returns the rest by score.
  The floor was tuned on Clef with `eval/recall_rerank`, at one claim per request: batching claims
  into one request scored relevant claims under 0.2, so `RERANK_CHUNK` is part of the tuning too.
  Any other model reorders but drops nothing (`relevantFloorFor`). The rerank fails open to keyword
  order and only sees the pool, so a claim the keyword search misses still can't come back.
  A pointer hit (`Claim.pointer`: the query named the claim's path, anchor or ref) skips the model
  and stays first, and the model reads the whole claim, so it can't drop the memory the query named
  or miss the words that matched.
- Each recall with a query costs up to `RERANK_POOL` model requests, `RERANK_IN_FLIGHT` at a time
  under one 5 s deadline, each carrying at most `RERANK_QUERY` chars of the caller's query. There is
  no per-caller budget yet, so a token that can call `recall` can spend the install's AI quota;
  `KYPP_RERANK=off` turns the rerank off and keeps the write gate.
- The write gate fails open, so defrag re-judges agent claims with no `metadata.s1` verdict
  (`judgeLate`) and rejects control text aimed at agents. A claim stored without `metadata.s1` is
  re-judged every pass until one sticks, so new write paths must keep recording the verdict.
  With an S1 model wired (`D1Store` `screened`), other sessions don't see a candidate until it has a
  verdict: recall, the briefing's recent sections and `nearSubjects` all apply `judgedOnly`. A new
  read of other sessions' candidates must apply it too, or control text written during a gate
  outage reaches other agents before `judgeLate` sees it.
- `/v1/systemone` is routed in `index.ts` before the OAuth provider, so it checks the bearer
  itself: a GitHub Actions OIDC token, else a `KYPP_S1_TOKENS` token (never `KYPP_API_TOKENS`, so a
  CI caller can't reach memory). The OIDC `aud` must equal `KYPP_PUBLIC_URL` with trailing slashes
  stripped, byte for byte. A caller that requests the token with its `TYPESAFE_BASE_URL` as the audience
  gets a 401 when that URL has a path or a different host. An empty `KYPP_OIDC_OWNERS` refuses all OIDC
  (fail closed). It is per-install, so it lives on the Worker, not in `wrangler.jsonc`;
  `keep_vars: true` stops a deploy from wiping it (or `KYPP_PUBLIC_URL`).
  An unknown `kid` refetches the JWKS at most once a minute, so a forged kid can't make the
  Worker hammer GitHub; a just-rotated key can 401 for up to that minute. Fork PRs get no OIDC
  token. typesafe-sdk needs `model` and `usage` in every reply, so the
  proxy fills them when Workers AI leaves them out; the SDK always sends a model (`jev-latest` if
  `TYPESAFE_DEFAULT_MODEL` is unset), which this route refuses with 400.
- `origin` is what the writer said and is never updated; `project` is where the claim is filed.
- A claim from an unregistered repo stays unsorted, and consolidation skips unsorted claims, so it
  never promotes. Register repos with the operator `register` op (`kypp project add`), which also
  files the claims already waiting under that origin; an agent's call never creates a project.
  Change placement only through `store.file()`, which also writes `defrag_log`.
- `apply()` guards each subject group with the count and newest `updated_at` read at plan time;
  a group that changed since is skipped (CHECK on `consolidation_guard` rolls its batch back).
  Count/time can repeat after correction plus a new proposal in one millisecond: every target must
  also still be live in the original group. This is not full revision fencing; all-writer command
  cutover is still required. New status writers must preserve these guards.
- Promotion counts sessions that recur at least `KYPP_RECUR_GAP_MINUTES` apart. That is
  repetition, not independent evidence; signed session ids only stop invented ones.
- The S1 agreement check (`consolidate.ts`) can only hold a promotion back, never cause one.
- A `claim` reply names up to three nearby subjects in the same lane (`nearSubjects`, other
  sessions only). It records no usage on purpose: the content was written before the agent saw the
  subjects, so reusing one is not an echo. Re-claiming the same content (whitespace-normalized)
  under another subject in the same session supersedes the earlier candidate (`renamedFrom` read
  before the insert, then `withdraw`; `metadata.renamed_to`). A rename of an echo stays an echo: it
  inherits `echo_of` and gets no session stamp. Grouping is still by exact subject.
- `correct` bypasses the S1 gate and the two-session rule. `KYPP_CORRECT=off` (set in
  `wrangler.jsonc`) hides and refuses it; `.dev.vars` turns it back on for the smoke test.
- `wrangler.jsonc` must stay installable by the Deploy to Cloudflare button (README): no resource
  ids, no owner-specific vars, and only the button's secrets in `.dev.vars.example` (it prompts
  for every key there). Local dev and the CI smoke read `smoke.dev.vars`. With `KYPP_PUBLIC_URL`
  unset, `publicBase` takes the request's origin, so one OAuth provider is cached per origin.
- `npm run deploy` applies D1 migrations before `wrangler deploy` (the button's build runs it), so
  new code never runs against an unmigrated schema. Migrations go by binding (`DB`), not name.
- `/oauth/register` is open by design (MCP clients self-register), so `REGISTER_LIMIT` throttles it
  per IP in `index.ts` before the OAuth provider writes the client to KV.
- Keep status edits on existing maintenance/correction/operator paths; ordinary agent claims
  only insert. Hosted operator correction has its own atomic batch, not a later consolidation pass.
