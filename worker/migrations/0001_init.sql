-- Same tables as kypp/store.py SCHEMA, minus the local-only columns (embedding, verify).
CREATE TABLE IF NOT EXISTS memory_claims (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL,
  scope TEXT NOT NULL, project TEXT,
  agent TEXT, user TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  authority TEXT DEFAULT 'agent',
  confidence REAL DEFAULT 0.7, source_ids TEXT DEFAULT '[]',
  code_refs TEXT DEFAULT '[]',
  metadata TEXT DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_project_idx ON memory_claims(project);
CREATE INDEX IF NOT EXISTS claims_status_idx ON memory_claims(status);
CREATE INDEX IF NOT EXISTS claims_subject_idx ON memory_claims(subject);

-- Keyword recall. Claim text never changes after insert (status changes only), so rows are
-- written once alongside the claim and never updated.
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(claim_id UNINDEXED, subject, content);

CREATE TABLE IF NOT EXISTS claim_usages (
  id TEXT PRIMARY KEY, consumer TEXT NOT NULL,
  claim_id TEXT NOT NULL, project TEXT, scope TEXT,
  surface TEXT NOT NULL,
  query TEXT,
  score REAL,
  metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usages_consumer_idx ON claim_usages(consumer);
CREATE INDEX IF NOT EXISTS usages_claim_idx ON claim_usages(claim_id);
