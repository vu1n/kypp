---
id: store
paths:
  - "kypp/store.py"
  - "kypp/repository.py"
  - "kypp/backends.py"
  - "kypp/vocab.py"
entry:
  - "kypp/store.py"
  - "kypp/repository.py"
tests:
  - "tests/test_store_contract.py"
  - "worker/test/store.test.ts"
---
# Store

The legacy engine talks to a `ClaimStore` (`put` / `get` / `search` / `find_similar`, plus the
governance writes it already makes). The staged command API is separate (above). Three adapters implement it: pyturso (existing MVCC
files, vectors + `LIKE`), libsql (FTS5 bm25 fused with exact cosine), and stdlib sqlite3
FTS5 (the hosted SQL, no vectors). `vocab.py` is the single source for types, scopes,
statuses and authorities. `code_pointers` indexes a doc:// ref or symbol key together with
the file path, so search can go from a file to entries and from an entry to files.

## Gotchas
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
- `ground()` also reorders: a claim whose every anchor is unresolved (`stale`, the code is gone)
  sorts after live claims, except a pointer hit, which keeps its place because the query named
  that anchor. Recall and the briefing ground their whole candidate pool before the limit, so a
  live claim replaces a stale one rather than queueing behind it. The hosted Worker has no checkout,
  so it neither grounds nor demotes.
- Code refs resolve to `path:line` plus a one-line preview at recall. Never store or return
  file bodies; they go stale and burn context.
- Vector recall is an exact scan on purpose (pyturso and libsql). libsql can build a DiskANN
  index; that trades recall for speed and is the wrong quality choice at this corpus size.
- Memory is append-only: claims and observations are never deleted.
