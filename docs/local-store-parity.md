# Local store parity with the hosted Worker

Status: design, waiting for Vu's sign-off. No store code changes in this PR.

Vu's rule for this work: local should be the same as hosted. One engine family (SQLite FTS5 and `bm25()`), one schema, one visibility rule, one ranking, one tokenizer, one query builder.

## Engine

Use CPython's `sqlite3` in WAL mode, with `busy_timeout`. It runs the Worker's migrations and recall SQL unchanged. Drop pyturso.

Measured on this machine (Python 3.12.3):

| | pyturso 0.8.3 | libsql 0.1.11 (embedded libsql 0.9.22) | CPython sqlite3 3.45.1 |
|---|---|---|---|
| `SELECT sqlite_version()` | 3.50.4 | 3.45.1 (`sqlite_version_info` attribute says 3.42.0; the SQL function does not) | 3.45.1, `ENABLE_FTS5` |
| Worker migrations `0001`–`0004` | not run: no FTS5 | `executescript` succeeds | `executescript` succeeds |
| `CREATE VIRTUAL TABLE … fts5` | WAL/default: `no such module: fts5`. After `journal_mode=mvcc`: `Virtual tables are not supported in MVCC mode` | works | works |
| `bm25()`, default unicode61 (`"cafe"` matches `café`) | n/a | works | works |
| Worker's `recall` SQL and `ancestors` CTE (`depth < 3` returns team, org, root and not the fourth hop) | n/a | works | works |
| `vector32` / `vector_distance_cos` | works | works | `no such function: vector32` |
| `BEGIN CONCURRENT` | works. Two threads overlapping 0.3s both committed | `near "CONCURRENT": syntax error` | `near "CONCURRENT": syntax error` |
| `PRAGMA journal_mode=mvcc` | becomes `mvcc` | stays `delete` | n/a |
| Two processes, WAL, 0.4s overlapping `BEGIN IMMEDIATE`, busy timeout 5s | n/a (MVCC instead) | both committed; the second waited (0.40s and 0.83s) | both committed; the second waited (0.40s and 0.83s) |
| Existing MVCC file | native | `file is not a database` in sqlite3 | `file is not a database` in sqlite3 |

libsql-experimental 0.0.55 matched libsql 0.1.11 on FTS5, `bm25()`, and the rejection of `BEGIN CONCURRENT`.

`sqlite3` is the same family as the Worker's test double (`node:sqlite`) and as D1. libsql also runs the Worker SQL, and it is the only probed engine that has both FTS5 and `vector32`. Its Python driver is not the sqlite3 module (a cursor does not iterate), and keeping it only to preserve vectors keeps the second path this change exists to remove. pyturso cannot run the Worker DDL at all.

## What dropping MVCC loses

`BEGIN CONCURRENT` is issued in one place: `MemoryStore._write` in `kypp/store.py`. Comments in that file, `kypp/mcp_server.py`, `.brief/docs/features.md`, and `docs/spec-check-continuation.md` describe the same mechanism. Nothing else in the repo starts a thread or a process against the database. The store self-test opens two connections and writes them one after another in the calling thread.

So the in-repo parallel writer count is zero. The designed case is several OS processes (`kypp serve`, the CLI) sharing `~/.kypp/memory.db`. WAL plus `busy_timeout` still lets those processes all commit: the second waits for the first. What disappears is an overlapping commit. On pyturso, two threads both finished inside a 0.3s overlap. On sqlite3, the second process waited out the first writer's 0.4s transaction and then committed.

No caller depends on the overlap. Hosted D1 already serializes writers; the Worker keeps status changes on the cron pass so concurrent sessions only insert. Local writes would use a normal transaction and retry `SQLITE_BUSY`.

## Vectors

Drop them to match hosted recall: no `embedding` column, no `vector32`, no cosine order, no `similar_pairs`. Keyword recall becomes the Worker's FTS5 `MATCH` plus `bm25()`, not `LIKE`. `KYPP_EMBED_MODEL` goes away.

That removes a capability other code calls:

- `kypp/arbiter.py` phase 2, when `consolidate(..., semantic=<distance>)` is set. The default is `semantic=None`, so a normal consolidate does not call it. The arbiter self-test does, and asserts a cross-subject merge.
- `kypp correct --semantic` (`kypp/shell.py`).
- The MCP `consolidate` tool's `semantic` argument (`kypp/mcp_server.py`). `0` means off.

Default recall already ignores vectors unless `KYPP_EMBED_MODEL` is set, and then it ranks by cosine instead of `bm25()`. Hosted recall never does that. `memory-scope-decay` lists embeddings as open ("measure on real claims first"), not as the ranker. Search there is FTS.

This is the stop. Semantic dedup and vector recall are optional, and they are real call paths. Deleting them needs a yes before code lands. The alternative that avoids the stop is libsql, FTS5 for recall (same SQL as the Worker) and `vector32` only for `similar_pairs`. That keeps two retrieval mechanisms in the local store.

## Decisions

No active decision has to change for the engine swap or for dropping MVCC.

- `turboquant-deferred` is superseded (retired 2026-10-04). It is an evaluation record, not a rule. The Store gotcha that points at it gets updated when the code changes.
- `additive-migrations` is superseded. The shared schema can be the Worker migration files.
- `memory-scope-decay` says the local-versus-cloud engine is not decided there. It specifies FTS search and reads that cascade over origin, roll-up groups, user, and global. Pointing local recall at that conforms. Local `vocab.Scope` is still only `project` and `global`; widening it to the Worker's `user` and `group` follows this decision.
- `append-only-history` says observations are the raw append-only layer and forbids `DELETE` against `observations`. The table stays.
- `verify-operator-only` and `authority-order` require the local `verify` field and `MemoryStore.mark_verified`. The column stays. The Worker still must not accept a `verify` argument. MCP behavior does not change.

Dropping `observations` or `verify` to make the schema a byte copy of today's Worker migrations would need amendments. The shared schema should grow by those two local pieces (a nullable `verify` column and the `observations` table) so both engines load one file and the decisions still hold. The Worker keeps ignoring them.

## After sign-off

One SQL source, the Worker migration files, loaded by `node:sqlite` in tests and by Python `sqlite3` locally. Local recall, visibility, ranking, and `ftsQuery` call that SQL:

- visibility: global, this project, `project_parents` ancestors up to 3 levels, this user, unsorted claims only for the writing session
- order: `bm25(claims_fts)`, then accepted, then scope distance, then confidence, then `updated_at`
- limit clamped to 1–50
- tokenizer: FTS5 default `unicode61` (the Worker DDL does not pass `tokenize=`; do not add a second tokenizer)
- handles: 8–32 hex, matching the Worker. Code-anchor grounding on `get` stays a local resolver pass over the same rows; it is not a second search index

Existing MVCC files do not open in sqlite3 (`file is not a database`, plus a `-log` sidecar). A pyturso database left in WAL mode does open. `MemoryStore` forces MVCC today, so a real `~/.kypp/memory.db` needs a one-shot dump through pyturso into a new sqlite3 file, not an in-place `ALTER`. The repo has no checked-in database. The dump should ship anyway, because the default path is documented and those files will not open on the new engine.

## Out of scope

Open PR #32 (cooperation contract, FTS5 over a different table) is a separate branch. This change does not edit `kypp/cooperation.py`, `worker/src/cooperation.ts`, or that fixture. A later implementation should not retokenize `claims_fts` away from unicode61, which that contract assumes.
