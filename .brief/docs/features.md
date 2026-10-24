---
id: features
type: features
title: "kypp feature map"
---

# kypp feature map

What each part of kypp is, where its code lives, and what bites there. Address a feature as
`doc://kypp/features@latest#<id>`, and run `brief features <files>` to list the ones a diff
touches. Internal areas count as features too. When code moves, update `paths:` in the same
change, because `brief check` blocks any glob that matches nothing.

<!-- brief:anchor claim-transition-contract -->
## Claim transition contract and staged command adapters

```yaml
paths:
  - "specs/claim_state_machine/model.py"
  - "specs/claim_state_machine/v1.json"
  - "specs/claim_state_machine/cutover.py"
  - "specs/claim_state_machine/cutover-v1.json"
  - "kypp/claim_machine.py"
  - "kypp/claim_commands.py"
  - "worker/src/claim-machine.ts"
  - "worker/src/claim-commands.ts"
  - "worker/migrations/0010_claim_commands.sql"
  - "worker/migrations/0011_normalized_claim_commands.sql"
  - "tests/test_claim_state_machine_spec.py"
  - "tests/test_claim_commands.py"
  - "tests/test_claim_cutover_spec.py"
  - "tests/claim_trace_oracle.py"
  - "worker/test/claim-commands.test.ts"
  - "worker/test/claim-cutover.test.ts"
  - "worker/test/d1.ts"
  - "docs/claim-state-machine.md"
  - "docs/claim-cutover.md"
```

A contract ratified by #43 for lifecycle, acceptance, verification fencing and atomic commands.
The Python kernel and Worker port have isolated durable command adapters; live-store cutover is
still pending. `docs/claim-state-machine.md` names the completed checkpoint and remaining work.

### Gotchas
- The old reference path imports the Python kernel; it is not an independent oracle. Original
  portable vector expectations check outcomes; cross-language tests check the Worker port.
- `claim_command_*` projections and ledgers are isolated from legacy claims. No current route or
  cron calls them. Do not claim production conformance or partially switch a live store.
- Never persist the whole group's history in one value: D1 caps a string/row at 2,000,000 bytes.
  Group metadata stays small; claims and events are separate rows, and the existing receipt table
  is the sole durable retry authority. Mutation planning must not load event/receipt history.
- Normalized reads span queries: compare group revisions/metadata before and after to avoid mixed
  versions. Commit-time CAS remains mandatory even after a consistent read.
- A duplicate can commit between receipt miss and state load, not just at the write guard.
  Planning-stale recovery must consult the original receipt too; never re-plan at a newer revision.
- Canonicalize legacy global keys before migration head selection: local global rows can still
  carry their original project. Preserve that raw field and pointer document namespaces separately.
- Synthetic cutover inventory is not a migration. Unsorted project-null claims have no staged
  Group/File command yet; do not map them into one shared null group or silently discard them.
- Authorize current group reach before calling read/execute, including exact retries. An Actor
  fingerprint records trusted role input; it does not authenticate the caller or prove current reach.
- Migration 0011 upgrades only prior experimental command snapshots, retaining all claims/events
  and original receipts. Stop old experimental writers first; it does not migrate live memory_claims.
- Size-limit and rollback checks use local SQLite/D1 stand-ins, not the deployed D1 service. Large
  claim groups, individual payload bounds, query budgets and deployment latency still need validation.
- Pyturso DDL cannot run in `BEGIN CONCURRENT`; bootstrap idempotently before command transactions.
- Python `str.split` and JavaScript `\s` differ on NEL, C0 separators and BOM. Exact recurrence
  agreement uses the same explicit whitespace set; shared port tests cover it.
- Actor capabilities, the separate Stamp argument, and typed agreement are trusted adapter inputs,
  not client-selected permissions. Authenticate and authorize the exact group before invoking it.
- Retirement is terminal; check outcomes qualify live read eligibility. Viewing is not acceptance.
- Maintenance can reject unaccepted agent candidates, never accepted answers. Recurrence can reuse
  older alternatives alongside a current-generation observation. Check tokens fence their issuance
  generation; a blocked candidate pass records evidence without acquiring verified authority.
- Each amended anchor stages activation at complete adapter/store cutover. A contract-only merge
  does not require existing handlers to have already switched semantics.
- SQL interleavings and rollback tests cover all three local drivers and the D1 stand-in; they do
  not prove deployed D1 isolation, authentication, shell/model truth, or an all-writer cutover.

<!-- brief:anchor store -->
## Store

```yaml
paths:
  - "kypp/store.py"
  - "kypp/repository.py"
  - "kypp/backends.py"
  - "kypp/vocab.py"
```

The legacy engine talks to a `ClaimStore` (`put` / `get` / `search` / `find_similar`, plus the
governance writes it already makes). The staged command API is separate (above). Three adapters implement it: pyturso (existing MVCC
files, vectors + `LIKE`), libsql (FTS5 bm25 fused with exact cosine), and stdlib sqlite3
FTS5 (the hosted SQL, no vectors). `vocab.py` is the single source for types, scopes,
statuses and authorities. `code_pointers` indexes a doc:// ref or symbol key together with
the file path, so search can go from a file to entries and from an entry to files.

### Gotchas
- pyturso executes lazily. A statement only runs once its cursor is fetched, which is why
  DDL and PRAGMAs are followed by `.fetchall()` or `.fetchone()`.
- With no `KYPP_EMBED_MODEL`, claims are stored without embeddings. Turso recall then uses
  `LIKE`. libsql and sqlite use FTS5 `bm25` instead. `capabilities.semantic` is about the
  backend, not about whether an embedder is wired.
- `KYPP_STORE=auto` opens an existing pyturso file as pyturso (sqlite3 cannot read it) and
  creates a new file as libsql. `sqlite` is the hosted-compatible adapter: `find_similar`
  raises, and `consolidate --semantic` keeps exact-subject dedup and says vectors did not run.
- `_write` retries transient conflicts/busy/locked errors up to 5 attempts. Pyturso 0.8.1 maps
  `Write-write conflict` at COMMIT to plain `DatabaseError`, so that exact case is retried too;
  other generic database errors are not. A command retry reruns its SQL revision guard.
- Filter project/scope/type/status before lexical and pointer LIMITs, not after merging a capped
  global candidate list. Otherwise an eligible match can disappear behind other projects' hits.
- Existing sqlite/libsql rows are backfilled into FTS once at bootstrap. New pointer and FTS rows
  ride the claim transaction; a refused room share creates neither.
- A full doc:// pointer lookup includes project and document, not just its anchor. Bare-anchor
  lookups are intentionally broad; they are discovery, not mutation or authorization.
- `code_pointers` does not store line numbers. A path is a hint. Recall still re-resolves
  a code anchor to `path:line` against the live tree.
- D1 `recall` must quote word tokens before FTS5 `MATCH`. Binding a raw path throws
  `syntax error near "/"`, so the pointer rank never runs. The raw path, anchor, or
  doc:// ref is the identity match and ranks ahead of bm25.
- Existing-table column changes remain additive/defaulted; new staged command tables do not
  migrate existing rows or make old writers safe. Coordinate all writers at activation.
- Code refs resolve to `path:line` plus a one-line preview at recall. Never store or return
  file bodies; they go stale and burn context.
- Vector recall is an exact scan on purpose (pyturso and libsql). libsql can build a DiskANN
  index; that trades recall for speed and is the wrong quality choice at this corpus size. Read
  `.brief/docs/turboquant-deferred.md` before adding one.
- Deletion rules are in `doc://kypp/append-only-history@latest#never-delete`.

<!-- brief:anchor recall -->
## Recall, briefing and handles

```yaml
paths:
  - "kypp/view.py"
  - "kypp/shell.py"
  - "kypp/remote.py"
```

`kypp recall`, `briefing`, `show`, `remember`, `correct`, `reject` and `usage` print one compact
handle line per claim, and `show` expands a handle to the full claim. With `KYPP_URL` set, all but
`usage` go to the hosted Worker (`kypp/remote.py`) and never open the local store.

### Gotchas
- The briefing is the same top-N accepted claims for every task. It is not task-conditioned
  (that's the unbuilt `compose`).
- `kypp usage --record` writes usage rows, but nothing in kypp ever fills `claim_usages.score`.

<!-- brief:anchor mcp -->
## MCP server

```yaml
paths:
  - "kypp/mcp_server.py"
```

`kypp serve` runs the agent-facing surface over stdio, or over HTTP with `--http`. It is bound
to one project, derived from `KYPP_REPO_ROOT`.

### Gotchas
- `serve --http` has no authentication. Keep it on localhost.
- The MCP `correct` tool lands at agent authority, not human. It is not a writer of `human` or
  `verified`. Local human authority is the operator's `kypp correct`; local verified authority is
  `MemoryStore.mark_verified`. Authenticated `POST /sync` is not built yet; when it lands, it is the
  import path for a ratified Brief revision at the archive stamp's authority
  (`doc://kypp/authority-order@latest#authority-dominates`).
- No agent call self-accepts. Every MCP `claim`, decisions included, lands as a candidate stamped
  `session:<id>`; it is accepted when a second session claims the subject or an operator accepts it.
  The stamp and usage logging need a session id: stdio mints one per process, `--http` needs
  `KYPP_SESSION`.

<!-- brief:anchor hosted-worker -->
## Hosted Worker

```yaml
paths:
  - "worker/**"
```

The optional hosted store: a Cloudflare Worker over D1 serving `briefing`, `recall`, `claim`,
`expand` and `correct` as a remote MCP server behind OAuth, so cloud sessions share one memory.
`worker/src/memory.ts` ports the arbiter's ranking and promotion rules; an hourly cron runs the
defrag pass (`worker/src/defrag.ts`: file unsorted claims, then consolidate). It adds a `user` scope (the signed-in person, every repo) beside `project` and
`global`, and an optional System One write gate (`worker/src/s1.ts`, TypeSafe JS SDK).
`worker/src/systemone.ts` serves the same Clef model to other tools at `POST /v1/systemone` in the
TypeSafe API's shape, for GitHub Actions OIDC tokens (`worker/src/oidc.ts`) and static `KYPP_S1_TOKENS`.

### Gotchas
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
- The `room` scope (memory-scope-decay §11) is Worker-only too, and also keys on `user` (as
  `room:<integration>:<id>`). Any query that guards `scope != 'user'` must guard `room` as well, or one
  room's lane leaks to other callers through that path; `roomVisible` and `get(…, room)` are the only
  reads a room token gets.
- The S1 gate runs Clef on the `AI` binding unless `TYPESAFE_API_KEY` is set. Workers AI is always
  remote, so plain `wrangler dev` fails without a Cloudflare login; `npm run dev:local` (and CI's
  smoke) strips the binding and uses the TypeSafe mock from `.dev.vars`.
- The S1 model fails open. It filters and labels writes and files unsorted claims into a registered
  project; it must never accept or widen a claim
  (`doc://kypp/memory-scope-decay@latest#scope-keys-decay`).
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
- `/v1/systemone` is routed in `index.ts` before the OAuth provider, so it checks the bearer
  itself: a GitHub Actions OIDC token, else a `KYPP_S1_TOKENS` token (never `KYPP_API_TOKENS`, so a
  CI caller can't reach memory). The OIDC `aud` must equal `KYPP_PUBLIC_URL` with trailing slashes
  stripped, byte for byte; brief's CI template requests the token with its `TYPESAFE_BASE_URL`, so a
  base URL with a path or a different host is a 401. An empty `KYPP_OIDC_OWNERS` refuses all OIDC
  (fail closed). It is per-install, so it lives on the Worker, not in `wrangler.jsonc`;
  `keep_vars: true` stops a deploy from wiping it (or `KYPP_PUBLIC_URL`).
  An unknown `kid` refetches the JWKS at most once a minute, so a forged kid can't make the
  Worker hammer GitHub; a just-rotated key can 401 for up to that minute. Fork PRs get no OIDC
  token, so brief triage escalates there. typesafe-sdk needs `model` and `usage` in every reply, so the
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

<!-- brief:anchor portable-cooperation -->
## Portable cooperation

```yaml
paths:
  - "kypp/cooperation.py"
  - "worker/src/cooperation.ts"
  - "worker/test/cooperation-contract.test.ts"
  - "worker/test/fixtures/cooperation/v1.json"
  - "worker/test/fixtures/cooperation/diagnostics.json"
  - "worker/test/fixtures/cooperation/regressions.json"
  - "tests/test_cooperation_contract.py"
  - "docs/cooperation-context.md"
```

Short-lived handoffs and evidence references shared by the Python and Worker
contracts. They are not claims. The store, selector, and hosted MCP wiring are
later slices; this feature is the frozen schema, validators, and fixture.

### Gotchas
- Do not write these records into `memory_claims`. Wall-clock expiry never deletes a
  row and does not change durable-claim clocks
  (`doc://kypp/memory-scope-decay@0011#scope-keys-decay`).
- `append_context` and `query_context` have no authority parameter. The input schemas
  set `additionalProperties` false
  (`doc://kypp/agent-mcp-surface-compose@0003#compose-surface`).
- Canonical JSON is sorted keys, no whitespace, and safe integers only. Both languages
  must match `worker/test/fixtures/cooperation/v1.json`.
- The idempotency digest is the normalized client payload. `derived_from` is sorted.
  An omitted ttl and an explicit 86400 are the same digest. Evidence identity is a
  different hash and ignores bridge order.
- A missing link and a forbidden link both fail as `INVALID_RECORD`.
- `legacy_false` is `inconclusive_execution`, not `invalid_candidate`.
- Search is FTS5 `unicode61` with `bm25()`, selector `cooperation-fts5-bm25-v1`.
  SQLite rejects `MATCH` inside `OR`, so exact work-ref rows are a second filtered
  read. Each statement `LIMIT`s to the window plus one before the merge. The extra
  row only detects the cap. Exact match uses whichever of repository and
  `candidate_revision` was supplied. The 100-row cap is the ratified maximum,
  ranked by exact match then `bm25()` then id, not the 100 newest rows.
- `validateContribution` returns the rebuilt records, with omitted nullable fields
  filled. `budget_bytes` is the canonical size of that normalized object.
- Contribution timestamps must be strings without coercion: `String([timestamp])`
  hides malformed JSON that Python rejects. Shared cases in `regressions.json`
  reseal the byte budget before testing rejection.
- Cooperation query tokens must retain combining marks, as claim tokens do. Splitting
  decomposed `nai\u0308ve` into `nai` and `ve` loses a valid `unicode61` match;
  `regressions.json` covers both token output and SQLite retrieval in both languages.
- Append schemas accept null lineage links just like runtime validators; null and
  omission must retain the same retry digest.
- Conflict diagnostics validate provenance tokens, full record IDs and bare SHA-256
  fingerprints. Both lists contain 2–16 distinct entries; they are independent sets,
  not paired arrays. Reseal negative test contributions so the byte budget cannot
  mask a diagnostic validation bug. Shared malformed cases live in `diagnostics.json`.
- Fixture case `floating-brief-ref` stores `doc://kypp/memory-scope-decay@latest#scope-keys-decay`
  so both validators reject it. `brief pin` rewrites that counterexample to the latest revision,
  and the case then accepts. Restore `@latest` after pin. It is not a citation.

<!-- brief:anchor capture -->
## Capture and sweep

```yaml
paths:
  - "kypp/wire.py"
  - "kypp/autocapture.py"
  - "kypp/_pillbox.py"
```

`kypp capture` turns one §0 session log into observations, plus claims when run with
`--distill`. `kypp sweep` captures every completed pillbox session, idempotently.

### Gotchas
- `sweep` treats a session as complete only once its log has a terminal event (`scored`,
  `run_finished` or `run_failed`). In-flight sessions are skipped, not partially captured.
- `sweep` derives each session's project from its pillbox log path, not from
  `KYPP_PROJECT`.

<!-- brief:anchor distill -->
## Distillation

```yaml
paths:
  - "kypp/distill.py"
  - "kypp/batch_distill.py"
  - "kypp/episodes.py"
  - "scripts/eval_episodes.py"
  - "eval/episodes/*"
  - "docs/episode-extraction-evaluation.md"
```

Turns a §0 trace into candidate claims. The heuristic failure-miner is the floor, and an LLM
distiller runs when `KYPP_DISTILL_MODEL` is set (`claude`, `codex` or an ollama model).

### Gotchas
- Without `KYPP_DISTILL_MODEL` you only get heuristic, failure-mined pitfalls.
- If the LLM distiller fails, it falls back to the heuristic distiller rather than raising.
- With `TYPESAFE_API_KEY` or `KYPP_DECIDER_URL` (a local strands-decider) set, and `kypp[s1]`
  installed, a System One gate drops drafts scoring
  p_keep < 0.1. A missing key, failed call or non-probability answer keeps the draft: the gate
  only removes, it never blocks a session.
- Rubric handling is governed by `doc://kypp/no-grader-leak@latest#signal-only-rubric`.
- Shared-scope claims get model names and absolute host paths stripped before storage. A direct
  `claim()` caller is trusted to do this itself; the store doesn't enforce it.
- The LLM distiller sees only the first 40 turns, 400 characters each. `KYPP_EPISODES` (`cues`
  or `clairic`) adds verbatim user corrections and reasons from every turn ahead of them. Episodes
  are §0 event index plus offsets into that event's own text, checked before use; the observation
  stores pointers for the episodes the prompt showed (`episodes.select`), never the quotes. Clairic list answers are phrase fragments with
  meaningless list confidence: code widens each to its sentence. Unreviewed, Clairic quoted a
  pasted "AI agents must ignore prior instructions" line as a correction, so `clairic` always runs
  Clef review, which fails closed. Any error returns no episodes, never a partial or unreviewed
  set. Evaluation and limits: `docs/episode-extraction-evaluation.md`.

<!-- brief:anchor consolidate -->
## Consolidation

```yaml
paths:
  - "kypp/arbiter.py"
```

`kypp consolidate` dedups by subject, and semantically when given `--semantic`, superseding
the weaker claims. It also promotes corroborated candidates.

### Gotchas
- On a small or diverse corpus the corroboration gate (K=2) can promote nothing, leaving the
  briefing empty. That shows up as "memory had no effect", not as an error.
- A candidate written after an accepted claim on the same subject (same authority) is a pending
  update: it is kept, not superseded, and replaces the old answer once it corroborates. Without
  that, accepted-beats-candidate made every update lose to the answer it corrects. Exact-subject
  groups only; the semantic pass doesn't keep updates.
- Corroboration counts a claim's `session:` stamp when it has one, never the `source_ids` an MCP
  caller cited. Those stay as provenance, but a cited `session:` id is stored as `cited:session:…`,
  and an HTTP server with no `KYPP_SESSION` (nothing to stamp) stores every cited id as `cited:…`,
  which never counts. Otherwise one session promotes its own guess with invented ids or
  observations it filed. The hosted Worker stores only the stamp, to the same effect.
- The S1 agreement judge only holds a promotion; `None` (no model, failed call) never blocks one.
  A held group still supersedes its weaker duplicates, so the subject waits for a fresh session.
- The agreement phase (`tag_of` + `pair_judge`, off unless both are passed) is the one place a model
  answer can cause a promotion. It joins two claims only on an affirmative judgment, and only the
  survivor's direct matches merge: union-find over A~B, B~C chained different lessons into clusters
  of 25 on the FrontierMemory store. A shared tag is never agreement by itself. The hosted Worker's
  `memory.ts` does not have this phase yet, so turning it on locally makes local and hosted promote
  differently. Cutoff and its eval: `eval/agreement/`.

<!-- brief:anchor verify -->
## Verify

```yaml
paths:
  - "kypp/verify.py"
```

`kypp verify` re-runs each claim's `verify` shell command. A pass marks the claim accepted
and verified; a failure rejects it, and it revives if a later run passes.

### Gotchas
- Governed by `doc://kypp/verify-operator-only@latest#operator-only-verify`. Never run it
  against a store whose verify commands you didn't vet.

<!-- brief:anchor transcripts -->
## Transcript seeding

```yaml
paths:
  - "kypp/transcripts.py"
  - "kypp/seed.py"
```

`kypp seed <repo>` bootstraps memory from Claude Code and Codex transcript history, converted
into §0 events.

### Gotchas
- `seed` skips eval-contaminated sessions, because those may become eval tasks. A session
  that both seeds memory and becomes an eval task invalidates every lift measurement.
- Codex transcripts can repeat a `call_id`. Duplicates collapse, but distinct calls without
  an id must not.

<!-- brief:anchor eval-mining -->
## Eval-task mining

```yaml
paths:
  - "kypp/evaltasks.py"
```

`kypp mine-tasks <repo>` mines eval-task candidates from transcripts. A session whose tests
pass becomes an auto-gradeable candidate; one without a grader is flagged TODO.

<!-- brief:anchor cli -->
## CLI entrypoint

```yaml
paths:
  - "kypp/cli.py"
  - "kypp/__init__.py"
  - "pyproject.toml"
  - "uv.lock"
```

`kypp <command>` dispatches to each module's `main()`. Imports are lazy, so `serve` never
loads the capture stack.

<!-- brief:anchor spec-consistency -->
## Spec consistency

```yaml
paths:
  - "speccheck/**"
  - ".github/workflows/spec-consistency.yml"
```

`python -m speccheck check` compares head code to a pinned baseline spec and writes an advisory
report. CI uploads that report and exits 0 when the checker itself finishes, findings included.

### Gotchas
- The checker never writes `.brief/` or `.brief/SIGNOFF`. A report path under `.brief/` is refused.
- Rule text comes from the baseline commit. `--baseline HEAD` is ignored when that commit's spec
  differs from `origin/main` or `main`, so a change is not judged against its own rules.
- Semantic scanners do not emit `bounded`. `no_counterexample` is not assurance. `mcp.add_tool`
  counts when the callable resolves to one function in the file; an unknown or ambiguous callable
  stays `unresolved`. Bare `**kwargs` or `*args` stays `unresolved`. `Unpack[TypedDict]` and a
  pydantic/TypedDict parameter are checked when that model is defined in the tree with a closed
  field list; an open, inherited, or imported model that does not resolve stays `unresolved`.
  A model is closed only when every `extra` or `model_config` setting is absent or a proved
  literal `forbid` or `ignore`. `extra = Extra.allow`, a non-literal `model_config`, or any other
  unproved extra policy stays `unresolved`. The only bounded MCP proposition is a fully parsed
  `TOOLS` initializer that declares no `verify` property.
- The authority-order writer bullets stay one claim: `shell.correct_main` is grouped with the
  following `POST /sync` bullet, and `human-authority-writers` stays `unresolved`.
- Rank, MCP authority, and human-writer rules still read frozen `authority-order@0004`.
  `verified-authority-writer` reads `authority-order@0006`. Ratifying that policy is not
  positive evidence: a scan with no proved write and no gap is `no_counterexample`.
- `authority-rank-order` reads `kypp/vocab.py`, `kypp/arbiter.py`, and the `worker/src/memory.ts`
  mirror. The TypeScript order has to be proved equal to the Python order (`agent` < `verified` <
  `human`, higher rank wins). Divergence is a violation. A missing file, a parse failure, or an
  expression the checker cannot classify stays `unresolved`. A recognized matching `compareRank`
  is `no_counterexample`, not assurance. An earlier return, an extra branch, or an incidental
  `rank(` call stays `unresolved`. Negation or `reversed` on the Python `_rank` is a violation.
  Runtime order is the survivor oracle in `tests/test_rank_oracle.py` and
  `worker/test/rank-oracle.test.ts`; an early return on confidence fails that oracle.
- Stale `doc://` pins are one advisory per pin. The detail lists every path and line.
- `no-delete-claims` checks SQL for a delete of `memory_claims` or `observations`
  in `kypp/**/*.py` and `worker/**/*.{ts,sql}`. Bracket, backtick, and double-quoted identifiers
  count, including a schema prefix (`main.[memory_claims]`). Constant `+`, templates, and `"".join`
  are folded before the scan, in Python and in TypeScript; the leaves of that fold are not judged
  on their own. There is no SQL allow-list and no blanket pass. Only DELETE, REPLACE, INSERT OR
  REPLACE, DROP, and TRUNCATE can be candidates, and only when the target is protected or unproved.
  A proved `DELETE` is a violation even when a sibling statement is opaque. A parser failure, or a
  trailing hole on a statement that is not itself delete-like, stays `unresolved` and is named in
  coverage. CREATE, ALTER, INSERT, and SELECT that only name a protected table produce no delete
  finding. `replace()` in a read-only query is the SQL function, not a REPLACE statement.
  A `?` placeholder is unknown. Docstrings are not SQL. The recall/briefing exclusion
  bullet and the consolidation bullet stay unsupported. On the live tree the rule is `unresolved`:
  `worker/src/store.ts` updates `memory_claims` through a trailing `${UNSORTED}` hole.
- `verified-authority-writer` reads the `MemoryStore.mark_verified` bullet of
  `authority-order@0006`. The allowed writer is `kypp.store.MemoryStore.mark_verified`
  (`kypp/store.py`), not a class of the same name in another module. A proved
  `authority='verified'` assignment outside that method is a violation, including
  `ON CONFLICT DO UPDATE` and a one-step local bound to `"verified"`, and only when the
  SQL writes `memory_claims`. `UPDATE build_jobs SET authority='verified'` is not a
  violation. `POST /sync` is not built, so it is not an allowed writer. An unclassified
  write on claim storage, including a `?` binding, stays `unresolved` and is named in
  coverage. No proved write and no gap is `no_counterexample`, not assurance. A
  `WHERE authority='verified'` filter is not an assignment. The `CASE` inside
  `kypp.store.MemoryStore.mark_verified` stays allowed. On the live tree the rule stays
  `unresolved`: dynamic authority writes are still unclassified.
- `delete-sql-v3` and `verified-writes-v2` are the cache namespaces for those scans.
  An older `delete-sql-v2` or `verified-writes` row, including one that classified
  `replace()` as delete-like, is not reused.
- `schema-add-column` does not emit assurance. Comments, strings, and quoted names cannot
  supply clauses. `NOT NULL` without `DEFAULT`, DROP, RENAME, and a simple `CREATE TABLE`
  are violations. A `CREATE TABLE` named as a schema-version table also violates
  `schema-idempotence`. That name inside a string or comment does not. `CHECK`, a
  parenthesized default, and other unknown syntax stay `unresolved`. A known counterexample
  survives an opaque sibling. A scan with no counterexample and no gap is `no_counterexample`.
- `no-grader-verbatim` checks `ClaimDraft` subject and content. A proved read of a criterion
  `name` or `feedback` is a violation. Text copied from model JSON stays `unresolved`.
- `memory-scope-decay` and `agent-mcp-surface-compose` have no `### Invariant` list, so their
  prose stays unsupported. Do not rewrite the stale `@0001` pins on `main` to silence that.
- Worker signatures use tree-sitter with the optional `speccheck` dependencies. From this checkout,
  install `.[speccheck]` or run `uv run --extra speccheck python -m speccheck check`.
- Worker evidence covers the module-level `const TOOLS` initializer's literal schema keys, not
  runtime immutability or global absence. Same-file schema references must be unambiguous; an
  extra use that may mutate or expose the schema leaves a coverage gap.
- Unknown Worker array entries, spreads, computed keys, duplicate enclosing fields, or unsupported
  schema constructs prevent conformance. A covered literal `verify` key still violates when
  another entry is unresolved. Comments and string contents are not tool declarations.
- Syntax cache keys include the checker parser version and installed runtime/grammar versions.
  Missing parser dependencies abstain without caching a result that could mask a later install.
- The original PR23 holdout is regression data. The frozen nine-case Worker holdout from #24
  stays in `speccheck/holdout_cases.py` and is not extended. False passes for the later rank,
  registration, SQL, and grader rules are the separate `gap-holdout` split. Smoke controls are
  not accuracy evidence. Provenance for the frozen nine is in `docs/spec-check-evaluation.md`.
- SQL migration evidence covers literal initializer entries and a bounded statement grammar.
  Comments, strings, and quoted names cannot supply clauses. Unknown syntax is `unresolved`.
  A known non-additive statement is a violation and survives an unrelated gap. The checker
  does not execute SQL. A clean scan is `no_counterexample`, not assurance. The
  additive-migrations@0002 bullet is retired. The check is not proof of database acceptance,
  default semantics, or runtime idempotence. Schema evaluation is in
  `docs/spec-check-schema-evaluation.md`.
- A reference-index cache hit for the same head SHA is reused only when the dependency hash
  still matches. A baseline spec change rebuilds the index.
- Source loading requires every selected file to be regular UTF-8 text. A read, decoding, or
  file-type failure aborts the check before report or cache writes. It is not a silent omission
  and it does not become an empty file or a `bounded` `doc-refs` result. A loaded broken
  reference violates. A complete scan whose refs resolve is `bounded`, not assurance.
- A selected path that `git ls-files -v` tags `S` or `s` and that is absent from the worktree
  is the same load failure. Sparse checkout and skip-worktree leave the path in `ls-files -c`
  without writing it to disk; that is not an intentional deletion and must not yield a `bounded`
  report. A selected skip-worktree path that is still on disk is read. An unselected omitted
  path stays existence-only and does not fail the check.
- Git filenames are NUL-delimited and literal. A worktree deletion of a materialized index entry
  leaves both content and path inventories; selected symlinks, including symlinked parent
  directories, are rejected.

<!-- brief:anchor governance -->
## Amendment registration

```yaml
paths:
  - "scripts/check_brief_amendments.py"
  - "tests/governance/**"
  - ".github/workflows/brief-amendments.yml"
```

The repo-owned PR check requires every live proposal to enter Brief's amendment workflow.
It uses the pinned Brief gate; it never approves or ratifies an amendment.

### Gotchas
- Brief v0.5.2 triage discovers decision edits, not standalone proposal files. Include the
  proposed decision edit and SIGNOFF entry together; a green gate alone is not ratification.
- Keep this workflow separate from generated `brief.yml`, so `brief init --ci` cannot erase it.
