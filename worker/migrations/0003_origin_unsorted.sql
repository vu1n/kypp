-- Placement (doc://kypp/memory-scope-decay#scope-keys-decay §8): a claim keeps the repo name it was
-- written from as an immutable origin. scope='unsorted' (project NULL) holds a claim whose origin is
-- missing or not a known project until it is filed.
ALTER TABLE memory_claims ADD COLUMN origin TEXT;
UPDATE memory_claims SET origin = project WHERE origin IS NULL;
CREATE INDEX IF NOT EXISTS claims_scope_idx ON memory_claims(scope);
