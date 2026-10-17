-- Staged command aggregates only. This does not migrate or activate memory_claims.
CREATE TABLE IF NOT EXISTS claim_command_groups (
  group_key TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_command_ids (
  claim_id TEXT PRIMARY KEY, group_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_command_receipts (
  principal TEXT NOT NULL, command_id TEXT NOT NULL, group_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(principal, command_id)
);
CREATE TABLE IF NOT EXISTS claim_command_guard (
  ok INTEGER CONSTRAINT claim_command_cas CHECK (ok = 1)
);
