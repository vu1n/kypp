---
id: additive-migrations
project: kypp
type: decision
status: active
title: "Evolve the store schema with additive, defaulted columns only"
related_code:
  - "kypp/store.py"
---

<!-- brief:anchor additive-schema -->
## Add columns with defaults; never NOT NULL, never a version table

Schema changes go in `_MIGRATIONS` as `ALTER TABLE … ADD COLUMN` with a default (or nullable). Re-running a migration is safe because "duplicate column" errors are swallowed.

**Why.** Many hosts open the same store file with different kypp versions. Additive, defaulted columns keep old rows valid and old readers working, with no migration ordering to coordinate.

### Invariant
- Every `_MIGRATIONS` entry is `ADD COLUMN`, and none is `NOT NULL` without a default.
- No schema version table; idempotence comes from swallowing duplicate-column errors only.
