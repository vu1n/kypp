-- apply() inserts a 0 here when a group changed since it was read; the CHECK fails and D1 rolls the
-- whole group's batch back. Rows are never actually stored.
CREATE TABLE IF NOT EXISTS consolidation_guard (ok INTEGER NOT NULL CHECK (ok = 1));
