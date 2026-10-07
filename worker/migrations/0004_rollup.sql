-- Roll-up (memory-scope-decay §8): which groups or orgs a project rolls up to. A project may sit in
-- several. Edited by the operator; regrouping never rewrites a claim. A group-scope claim is stored
-- with project = the group's name, and every project under that group reads it.
CREATE TABLE IF NOT EXISTS project_parents (
  project TEXT NOT NULL, parent TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (project, parent)
);
CREATE INDEX IF NOT EXISTS project_parents_parent_idx ON project_parents(parent);
