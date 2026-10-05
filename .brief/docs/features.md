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
- The MCP `correct` tool lands at agent authority, not human. Only the operator's `kypp correct`
  writes human authority (`doc://kypp/authority-order@latest#authority-dominates`).
- No agent call self-accepts. Every MCP `claim`, decisions included, lands as a candidate stamped
  `session:<id>`; it is accepted when a second session claims the subject or an operator accepts it.
  The stamp and usage logging need a session id: stdio mints one per process, `--http` needs
  `KYPP_SESSION`.

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
- With `TYPESAFE_API_KEY` set (and `kypp[s1]` installed), a System One gate drops drafts scoring
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
