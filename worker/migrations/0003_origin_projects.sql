-- Placement (memory-scope-decay §8). `origin` is what the writer said and never changes; `project` is
-- where the claim is filed. A project-scope claim with no project is unsorted until defrag files it.
ALTER TABLE memory_claims ADD COLUMN origin TEXT;
ALTER TABLE memory_claims ADD COLUMN session TEXT;
-- When defrag last tried and failed to file an unsorted claim, so each pass starts with the ones it
-- has waited longest on instead of re-asking about the same few.
ALTER TABLE memory_claims ADD COLUMN file_tried_at TEXT;
UPDATE memory_claims SET origin = project WHERE origin IS NULL;
CREATE INDEX IF NOT EXISTS claims_session_idx ON memory_claims(session);

-- The projects a claim can be filed into. Rows are added by the operator; an unknown name in a call
-- never creates one. `description` is what the filing model reads to tell projects apart.
CREATE TABLE IF NOT EXISTS projects (
  name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
-- Projects that already hold claims stay known.
INSERT OR IGNORE INTO projects(name, created_at)
  SELECT DISTINCT project, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM memory_claims WHERE project IS NOT NULL;

-- Every placement defrag makes, with the reason and score, so a move can be traced and undone.
CREATE TABLE IF NOT EXISTS defrag_log (
  id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, action TEXT NOT NULL,
  from_project TEXT, to_project TEXT, score REAL, reason TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS defrag_log_claim_idx ON defrag_log(claim_id);
