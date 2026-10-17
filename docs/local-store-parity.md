# Repository interface for the local and hosted stores

Status: retrieval adapters implemented; claim-command persistence staged, not activated. Vu rejected
the earlier pick (move local kypp onto sqlite3 and drop vectors, or move both sides onto one engine).
Each backend keeps the search it can actually run. See [the ratified state-machine contract and
cutover checklist](claim-state-machine.md) before changing mutation semantics.

Measurements below are from 2026-10-14, not claims about today's latest package releases. Adapter
integration and command conformance were checked on 2026-10-17 with Python 3.12.14, pyturso 0.8.1,
libsql 0.1.11 and Node 24.19.0; no Turso Cloud, live D1, deployment, or paid model calls were used.

## What was measured

Python 3.12.3. No call to Turso Cloud or a live D1.

| | pyturso 0.8.3 (original measurement) | libsql 0.1.11 | CPython sqlite3 |
|---|---|---|---|
| SQLite identity | reports 3.50.4 inside the engine | `sqlite_version_info` is 3.42.0 | 3.45.1, FTS5 compiled in |
| FTS5 `MATCH` / `bm25` | `Virtual tables are not supported in MVCC mode` | works, including `café` matching under `unicode61` | works |
| `vector_distance_cos` | works (`vector32`) | works (`vector32`) | no such function |
| DiskANN | not in this engine | `libsql_vector_idx` creates | no |

Upstream SQLite is [3.54.0](https://www.sqlite.org/releaselog/3_54_0.html) (2026-10-09). Its FTS5 notes add a tokenizer v2 API and `contentless_unindexed`; `bm25()` and `unicode61` are unchanged ([FTS5](https://www.sqlite.org/fts5.html)). Neither libsql 0.1.11 nor this machine's sqlite3 is 3.54. D1 documents FTS5, including `fts5vocab` ([SQL statements](https://developers.cloudflare.com/d1/sql-api/sql-statements/)).

libSQL's vector functions are `vector32` and `vector_distance_cos` (cosine distance = 1 − cosine similarity), with an optional DiskANN index queried through `vector_top_k` ([vector functions](https://docs.turso.tech/sql-reference/functions/vector), [AI and embeddings](https://docs.turso.tech/features/ai-and-embeddings)). The Turso rewrite's full-text search is Tantivy (`fts_match` / `fts_score`), not FTS5 ([FTS comparison](https://docs.turso.tech/sql-reference/functions/fts)). Tantivy itself is at 0.26.2 (2026-09-08) and is a strong BM25 library, but it is a second query language next to the Worker. sqlite-vec's latest stable is 0.1.9 (2026-03-31); DiskANN there is still the 0.1.10 alpha (2026-05-18). D1 cannot load that extension.

## Search quality for kypp

Claims are short lessons. Recall has to find a paraphrase and an exact token (`mark_verified`, a path, an anchor). Near-duplicate consolidation is cosine distance between claims, not a keyword overlap.

- **Hybrid BM25 + exact cosine, fused by reciprocal rank fusion**, is the best quality available here. BM25 ranks the rare token. Cosine ranks the paraphrase. RRF never compares those two scores. At a few hundred to ~10⁵ claims an exact scan is higher recall than DiskANN, which is an approximation.
- **FTS5 alone** (the Worker today) is the right lexical rank and cannot do `--semantic`.
- **Vectors + `LIKE`** (pyturso today) does the semantic pass and is a weak keyword rank.
- **A hand-rolled inverted index plus ANN** would reimplement tokenization, BM25, unicode, crash recovery, and a write lock. SQLite already has those. It would be less durable under two processes and would not rank better at this size.

libsql 0.1.11 is the local backend that can run both halves. sqlite3 FTS5 is the hosted-compatible backend and reports `semantic: false`. pyturso stays the opener for an existing MVCC file, which sqlite3 and libsql both reject as `file is not a database`.

## Interface

`kypp.repository.ClaimStore` is the **legacy** integration interface: `put`, `get`, `search`,
`find_similar`, pointer lookups and existing governance writes. It does not implement the new
transition contract. `kypp.claim_commands.ClaimCommands` is the staged read/execute boundary,
implemented by `LocalClaimCommands` and the Worker's `D1ClaimCommands`. It has no generic set-status
operation. Its tables remain isolated until every reader/writer and verifier completes cutover.
`StoreCapabilities` says `semantic`, `fts`, and `pointers`. Search SQL stays inside the adapter.
There is no ORM.

`KYPP_STORE=auto` (the default for `store_from_env`) opens an existing pyturso file with pyturso and creates a new file with libsql. `sqlite` selects the FTS5 adapter.

When `semantic` is false, `find_similar` raises `SemanticUnavailable`. `consolidate --semantic` still does exact-subject dedup and returns `semantic: "unavailable: …"`. Semantic dedup is not removed from the libsql or pyturso adapters.

## Pointers

Brief leaves `doc://<project>/<doc>@<rev>#<anchor>` in a code comment, pointing from a file back at an entry. `code_pointers` stores that as columns, not as a JSON blob:

- `entry_ref`, `project`, `doc_id`, `anchor`, `revision` identify the entry. A lookup by full ref, by the same anchor at another revision, or by the bare anchor hits the same rows.
- `path` and `symbol` identify the code. `entries_for_path` is code → entries. `paths_for_entry` is entries → code.
- `claim_id` is set when the edge belongs to a claim (a `code_ref`, or a doc ref written in the claim). A scanned comment can exist with an empty `claim_id`.
- `kind` is `context` or `code_ref`.

Line numbers are not a column. `memory-scope-decay` says a path is a hint and lines are not stored; recall still resolves a live `path:line` when it renders a claim. Search treats a path, an anchor, or a doc:// ref in the query as an identity hit and ranks those claims ahead of BM25 or cosine.

Worker migration `0009_code_pointers.sql` follows the room/operator migrations already on main.
Python and `D1Store.recall` quote Unicode letter/mark/number/underscore tokens before `MATCH` — a `/`
in a path is FTS5 syntax — and return visible pointer hits ahead of `bm25`. All callers pass raw text,
not a pre-built FTS expression. Project/scope/type/status filters run before candidate caps. FTS is
backfilled for existing sqlite/libsql rows. A full doc:// ref keeps project/document identity when
matching another revision; a bare anchor intentionally discovers all matching entries.
