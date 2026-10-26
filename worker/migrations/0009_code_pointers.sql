-- Code ↔ entry edges. entry_ref is the doc:// pin or a symbol:/path: key.
-- path is indexed on its own. Line numbers are not stored: they drift, and recall re-resolves them.
-- A claim's key is a feature ref; a file path is a hint, and line numbers are not stored.
CREATE TABLE IF NOT EXISTS code_pointers (
  id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL DEFAULT '',
  entry_ref TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  doc_id TEXT NOT NULL DEFAULT '',
  anchor TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  symbol TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS code_pointers_edge
  ON code_pointers(kind, entry_ref, path, symbol, claim_id);
CREATE INDEX IF NOT EXISTS code_pointers_path_idx ON code_pointers(path);
CREATE INDEX IF NOT EXISTS code_pointers_anchor_idx ON code_pointers(anchor);
CREATE INDEX IF NOT EXISTS code_pointers_entry_idx ON code_pointers(entry_ref);
CREATE INDEX IF NOT EXISTS code_pointers_claim_idx ON code_pointers(claim_id);
CREATE INDEX IF NOT EXISTS code_pointers_doc_idx ON code_pointers(project, doc_id, anchor);
