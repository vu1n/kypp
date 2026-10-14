# Local and hosted store engines

Status: design, waiting for Vu to pick. No store code in this PR.

Vu's rule: local and hosted should be the same engine family, schema, visibility, ranking, tokenizer, and query builder. He also asked why the Worker cannot use Turso. This note compares three placements and stops.

## Recommendation

Keep D1 on the Worker and move local kypp to CPython `sqlite3` in WAL mode (option a).

Turso Cloud hosts two engines. The Worker can call either of them over HTTP. Only libSQL runs the FTS5 SQL the Worker already ships. The Turso rewrite, which is the engine with concurrent writes, does not.

- **libSQL** (`@libsql/client`, `turso db create` without `--tursodb`). SQLite fork. [FTS5 is a built-in extension](https://docs.turso.tech/features/sqlite-extensions), so `MATCH` and `bm25()` are the SQLite FTS5 functions. The TypeScript reference lists concurrent writes as **not supported**. From a Worker this is `fetch`, not a binding. Embedded replicas need a filesystem, so the Worker cannot be one.
- **Turso rewrite** (`@tursodatabase/serverless`, `turso db create --tursodb`, and today's pyturso). MVCC concurrent writes. Full-text search is Tantivy: `fts_match` / `fts_score`, not `MATCH` / `bm25()`, and the tokenizers are `default`, `raw`, `simple`, `whitespace`, and `ngram`, not `unicode61`. [Turso's own comparison](https://docs.turso.tech/sql-reference/functions/fts) says this replaces FTS5. The offering is in early preview. pyturso 0.8.3, measured below, is this engine: `no such module: fts5`.

Moving the Worker onto libSQL-on-Turso runs the same SQL and adds an HTTP hop, a second account, and no MVCC. Moving it onto the rewrite means rewriting the search SQL. Neither is why the stores disagree today. They disagree because local pyturso cannot run the Worker DDL.

## Side by side

| | (a) D1 + CPython sqlite3 | (b) D1 + embedded libSQL | (c) Both on libSQL / Turso Cloud |
|---|---|---|---|
| Hosted engine | Cloudflare D1. [Documents the FTS5 module, including `fts5vocab`](https://developers.cloudflare.com/d1/sql-api/sql-statements/). The Worker SQL already calls `bm25(claims_fts)`. | Same D1 database. | libSQL database on Turso Cloud, reached with `@libsql/client/web` (`fetch`). FTS5 is documented as always on. Not measured against a live Turso database from this run. |
| Local engine | `sqlite3` 3.45.1, WAL, `busy_timeout`. Stdlib. | Python `libsql` 0.1.11 (embedded libsql 0.9.22). | Same embedded libsql, or an embedded replica (`sync_url` on `libsql.connect`). |
| Worker DDL and `bm25()` | Runs on D1 by the docs, and on local sqlite3 (measured). | Hosted as in (a). Local libsql ran migrations `0001`–`0004`, `bm25()`, and `"cafe"` matching `café`. | Hosted: FTS5 is documented, `bm25()` was not executed on Turso Cloud here. Local: same measurement as (b). The Turso **rewrite** does not run this DDL. |
| Tokenizer | FTS5 default `unicode61` on both. The Worker DDL does not pass `tokenize=`. | Same, as long as local recall uses FTS5 and not `vector32`. | Same on libSQL. The rewrite's FTS tokenizers are a different set. |
| Concurrency | D1 processes one database one query at a time ([D1 FAQ](https://developers.cloudflare.com/d1/reference/faq/)). Local WAL: two processes both committed; the second waited out a 0.4s transaction (0.40s and 0.83s). | Hosted as in (a). Local libsql, same WAL test, same wait (0.40s and 0.83s). | libSQL write transactions on a primary cannot run in parallel ([client reference](https://docs.turso.tech/sdk/ts/reference)). Replica writes are forwarded to that primary. MVCC exists on the rewrite, which is not this option. |
| Embedded replica | None. D1 read replicas are a Cloudflare feature and need the Sessions API. `env.DB` today does not use it, so Worker queries hit the primary. | None. A libSQL replica syncs from a libSQL primary, and the primary here is D1. | Worker: no. Replicas need a filesystem; Workers do not have one. Local: `libsql.connect(..., sync_url=, sync_interval=, offline=)`. Reads are local. Default writes go to the cloud primary, then back to that replica. Other replicas see them on `sync()`. `offline=true` writes locally first. Sync is page frames (4 kB). Turso warns not to open the file during sync. |
| Latency | Worker to D1 stays on Cloudflare's network. D1's own guide: an indexed point read is under 1 ms of SQL time; writes are several milliseconds plus durable commit. Local sqlite3 is in-process. | Hosted as in (a). Local libsql is in-process. | Every Worker statement is an HTTPS request (one request for a `batch`). Interactive transactions hold the write lock up to 5 seconds; kypp's `batch()` shape matches `client.batch(..., "write")` better than that. Local replica reads are local; a non-offline write pays the round trip to the primary. Not timed from this VM. |
| Cost | Workers Free: 5 million rows read/day, 100,000 rows written/day, 5 GB ([D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)). Workers Paid ($5/month) includes 25 billion reads/month and 50 million writes/month, then $0.001 / million reads and $1.00 / million writes. No egress charge. One small memory database sits inside the free daily caps. | Same bill as (a). The Python package is free. | Turso Free, [turso.tech/pricing](https://turso.tech/pricing), fetched 2026-10-14: $0, 100 databases, 5 GB, 500 million rows read/month, 10 million rows written/month, 3 GB of embedded sync, 1 day of point-in-time restore. Developer is $4.99/month (9 GB storage, 2.5 billion reads, 25 million writes, 10 GB sync, then overage). This is a second meter on top of the Worker, KV, and AI bill. A btree split replicates many 4 kB frames against the sync allowance. |
| Migration | Worker stays. Local `MemoryStore` stops forcing MVCC and loads the Worker SQL. Existing MVCC files do not open in sqlite3 (`file is not a database`, plus a `-log` sidecar). A one-shot dump through pyturso. Tests stay `node:sqlite` for the Worker and gain a Python run of the same SQL. | Same hosted migration as (a), plus a libsql driver that is not the sqlite3 module (a cursor does not iterate). Still a pyturso dump: an MVCC file is not a libsql file either. | Replace `D1Database` (`prepare` / `bind` / `batch` / `meta.changes`) in `store.ts`, `index.ts`, and the cron. Drop the `d1_databases` binding and `migrations_dir` in `wrangler.jsonc`. Add `TURSO_URL` and a token. Export the D1 database and import it. `wrangler d1 execute` examples in `worker/README.md` move to the Turso CLI. The `node:sqlite` test double stops being the production driver. Local dump as in (b). |
| What stays different | Hosted has no local `verify` shell column usage and no `observations` writer. Those columns can live in the shared schema; the Worker keeps ignoring them. | Same, and local libsql also has `vector32`, which D1 does not. Using it recreates a second recall path. | An embedded replica makes local and hosted one database. That is the sync question `memory-scope-decay` left open. It also uploads operator `verify` shell text to Turso. |

## Measurements already taken

Python 3.12.3 on this machine. No call to Turso Cloud or to a live D1.

| | pyturso 0.8.3 | libsql 0.1.11 | CPython sqlite3 3.45.1 |
|---|---|---|---|
| `sqlite_version()` | 3.50.4 | 3.45.1 (the Python attribute says 3.42.0) | 3.45.1, `ENABLE_FTS5` |
| Worker migrations `0001`–`0004` | no FTS5 module | `executescript` succeeds | `executescript` succeeds |
| `fts5` | WAL: `no such module: fts5`. After `journal_mode=mvcc`: `Virtual tables are not supported in MVCC mode` | works | works |
| `bm25()`, unicode61 | n/a | `"cafe"` matches `café` | same |
| Ancestors CTE, `depth < 3` | n/a | team, org, root; the fourth hop is excluded | same |
| `vector32` | works | works | `no such function: vector32` |
| `BEGIN CONCURRENT` | two overlapping threads both committed | syntax error | syntax error |
| Two processes, WAL, 0.4s overlap, busy timeout 5s | n/a | both committed; second waited | both committed; second waited |
| Open an MVCC file | native | `file is not a database` | `file is not a database` |

libsql-experimental 0.0.55 matched libsql 0.1.11 on FTS5, `bm25()`, and the rejection of `BEGIN CONCURRENT`. Python `libsql.connect` accepts `sync_url`, `sync_interval`, `offline`, and `auth_token`. That signature was not exercised against a cloud primary.

`BEGIN CONCURRENT` is issued only in `MemoryStore._write`. This repo starts no thread and no process against the database. In-repo parallel writers: none. Several OS processes may share `~/.kypp/memory.db`. WAL plus `busy_timeout` lets them commit; the second waits.

## Decisions

No active decision says the Worker must stay on D1, and none says it must leave.

- `memory-scope-decay` leaves "local vs cloud" open, and names `turso.sync` in pyturso 0.8 as one unfinished option. Option (c) with an embedded replica decides that. Option (a), (b), and (c) without a replica leave it open. The pyturso sync API and today's `@tursodatabase/sync` are the rewrite; libSQL replicas are the other mechanism. Neither is ratified.
- `verify-operator-only` and `authority-order` keep the local `verify` field and `MemoryStore.mark_verified`. A replica would store those shell commands on Turso. The decision's reason is that a verify command is code execution for whoever runs the sweep. The Worker does not run them. Shipping them to the cloud primary still needs an explicit yes.
- `append-only-history` keeps the `observations` table. Dropping it, or dropping `verify`, to match today's Worker migrations byte for byte would need amendments. The shared schema should grow by those two pieces. The Worker keeps ignoring them.
- `turboquant-deferred` and `additive-migrations` are superseded. Embeddings stay an open item in `memory-scope-decay`, not the ranker. Dropping local cosine recall so both sides order by `bm25()` removes `similar_pairs`, `kypp correct --semantic`, and MCP `consolidate(semantic>0)`. Default `consolidate()` does not call that path. The arbiter self-test does. That drop is part of matching the Worker, whichever option you pick, and it needs the same yes.

`.brief/docs/features.md` describes a Worker over D1. That is a feature map, not a decision. It gets edited with the code, after you pick.

## After you pick

One SQL source either way: the Worker migration files. Visibility, `ftsQuery`, and this order: `bm25(claims_fts)`, accepted, scope distance, confidence, `updated_at`, limit 1–50. Handles become 8–32 hex. Code-anchor grounding stays a local resolver pass over the same rows.

PR #32 (cooperation contract) is a separate branch. It assumes `claims_fts` stays `unicode61`. This note does not edit those files.
