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

<!-- brief:anchor store -->
## Store

```yaml
paths:
  - "kypp/store.py"
  - "kypp/vocab.py"
```

The tursodb-backed claim store holds observations, claims and usage rows. It handles
concurrent writes (MVCC + `BEGIN CONCURRENT`), hybrid recall (vector + keyword), and
re-resolves code refs at recall. `vocab.py` is the single source for types, scopes,
statuses and authorities.

### Gotchas
- pyturso executes lazily. A statement only runs once its cursor is fetched, which is why
  DDL and PRAGMAs are followed by `.fetchall()` or `.fetchone()`.
- With no `KYPP_EMBED_MODEL`, claims are stored without embeddings, and recall silently
  falls back to keyword `LIKE` matching.
- `_write` retries an MVCC conflict 5 times with no backoff. Only `turso.OperationalError`
  triggers a rollback.
- Schema changes are `ADD COLUMN` with a default only: no `NOT NULL`, no version table. Hosts
  on different kypp versions open the same store file.
- Code refs resolve to `path:line` plus a one-line preview at recall. Never store or return
  file bodies; they go stale and burn context.
- Recall is a linear scan in tursodb on purpose. Read the evaluation in
  `.brief/docs/turboquant-deferred.md` before adding a vector index; the next step is in-DB DiskANN.
- Deletion rules are in `doc://kypp/append-only-history@latest#never-delete`.

<!-- brief:anchor recall -->
## Recall, briefing and handles

```yaml
paths:
  - "kypp/view.py"
  - "kypp/shell.py"
```

`kypp recall`, `briefing`, `show`, `remember`, `correct`, `reject` and `usage` print one compact
handle line per claim, and `show` expands a handle to the full claim.

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
- The OAuth resource id is exactly `<KYPP_PUBLIC_URL>/mcp`. A path or query suffix on the MCP URL
  breaks token audience checks, so the project travels as a tool argument or the
  `X-Kypp-Project` header.
- The MCP endpoint is stateless: the session id from `initialize` is only echoed back, never
  stored. Don't add a Durable Object to hold sessions.
- The `user` scope exists only in the Worker; local `vocab.Scope` is still project/global. On the
  user scope the `user` column is the owner, so a static token's name decides whose user scope it reads.
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
- Recall shows other sessions' candidates in the caller's project by default (`candidates: "origin"`).
  The echo guard (`store.shownCandidate`) relies on `claim_usages` rows for what a session was shown,
  so every read path that renders claims must keep calling `recordUsage`.
- The briefing lists accepted claims at the caller's org (a group with no parent of its own) first,
  under "how this org works", and leaves nearer groups in the usual per-type sections.
- The briefing also lists the user's own unconfirmed `user`-scope claims from their other sessions
  (process and preferences), in every repo. A process lesson is often learned once per repo, so
  waiting for acceptance hid it. The echo guard covers user scope too: re-claiming a lesson the
  briefing showed doesn't count toward accepting it.
- The write gate fails open, so defrag re-judges agent claims with no `metadata.s1` verdict
  (`judgeLate`) and rejects control text aimed at agents. A claim stored without `metadata.s1` is
  re-judged every pass until one sticks, so new write paths must keep recording the verdict.
- `/v1/systemone` is routed in `index.ts` before the OAuth provider, so it checks the bearer
  itself: a GitHub Actions OIDC token, else a `KYPP_S1_TOKENS` token (never `KYPP_API_TOKENS`, so a
  CI caller can't reach memory). The OIDC `aud` must equal `KYPP_PUBLIC_URL` with trailing slashes
  stripped, byte for byte; brief's CI template requests the token with its `TYPESAFE_BASE_URL`, so a
  base URL with a path or a different host is a 401. An empty `KYPP_OIDC_OWNERS` refuses all OIDC
  (fail closed), and `wrangler deploy` overwrites dashboard vars, so set it in `wrangler.jsonc`.
  An unknown `kid` refetches the JWKS at most once a minute, so a forged kid can't make the
  Worker hammer GitHub; a just-rotated key can 401 for up to that minute. Fork PRs get no OIDC
  token, so brief triage escalates there. typesafe-sdk needs `model` and `usage` in every reply, so the
  proxy fills them when Workers AI leaves them out; the SDK always sends a model (`jev-latest` if
  `TYPESAFE_DEFAULT_MODEL` is unset), which this route refuses with 400.
- `origin` is what the writer said and is never updated; `project` is where the claim is filed.
  Change placement only through `store.file()`, which also writes `defrag_log`.
- `apply()` guards each subject group with the count and newest `updated_at` read at plan time;
  a group that changed since is skipped (CHECK on `consolidation_guard` rolls its batch back).
  Any new writer of claim status must go through `apply()` or bump `updated_at`.
- Promotion counts sessions that recur at least `KYPP_RECUR_GAP_MINUTES` apart. That is
  repetition, not independent evidence; signed session ids only stop invented ones.
- The S1 agreement check (`consolidate.ts`) can only hold a promotion back, never cause one.
- `correct` bypasses the S1 gate and the two-session rule. `KYPP_CORRECT=off` (set in
  `wrangler.jsonc`) hides and refuses it; `.dev.vars` turns it back on for the smoke test.
- `/oauth/register` is open by design (MCP clients self-register), so `REGISTER_LIMIT` throttles it
  per IP in `index.ts` before the OAuth provider writes the client to KV.
- The cron pass is the only writer that changes existing rows. Keep status edits there (and in
  `correct`, which consolidates one subject) so concurrent sessions only ever insert.

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
- The S1 agreement judge only holds a promotion; `None` (no model, failed call) never blocks one.
  A held group still supersedes its weaker duplicates, so the subject waits for a fresh session.

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
- Those writer rules still read frozen `authority-order@0004`. `authority-order@0005` reconciles
  the verified-writer contradiction, and that row has not been re-evaluated against it. Ratifying
  the policy does not create positive evidence.
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
  finding. A `?` placeholder is unknown. Docstrings are not SQL. The recall/briefing exclusion
  bullet and the consolidation bullet stay unsupported. On the live tree the rule is `unresolved`:
  `worker/src/store.ts` updates `memory_claims` through a trailing `${UNSORTED}` hole.
- `verified-authority-writer` stays `unresolved`. `POST /sync` may land a model-ratified revision
  at `verified`, and the next bullet says only `MemoryStore.mark_verified` sets `verified`. The
  writer set is not ratified. A proved `authority='verified'` assignment outside `mark_verified`
  is recorded on that unresolved verdict, including `ON CONFLICT DO UPDATE`. It is not a settled
  violation and not a pass. A `?` binding is a coverage gap. A `WHERE authority='verified'` filter
  is not an assignment. A one-step local bound to `"verified"` is recorded the same way.
- `schema-add-column` does not emit assurance. `NOT NULL` without `DEFAULT`, a second statement,
  or a version-table name is a violation. `ADD COLUMN` with a `CHECK` stays `unresolved`. A scan
  with no counterexample is `no_counterexample`.
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
- A reference-index cache hit for the same head SHA is reused only when the dependency hash
  still matches. A baseline spec change rebuilds the index.
