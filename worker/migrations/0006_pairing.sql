-- Paired room issuers (memory-scope-decay §11.7): an integration asks to pair, a person checks the
-- user code on kypp's page, the owner approves with the passphrase, and the integration redeems its
-- secret handle once for an issuer token. Only digests of the handle and the token are kept.
CREATE TABLE IF NOT EXISTS pairings (
  handle_digest TEXT PRIMARY KEY, user_code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, approved_at TEXT, denied_at TEXT, redeemed_at TEXT,
  -- The revoked paired grant the owner chose to continue, if any.
  resume TEXT
);
-- An issuer grant made by pairing. `name` is the issuer namespace its rooms are keyed under
-- (rooms.integration). Revoking it stops every token under it but keeps its rooms, so a later pairing
-- the owner approves can continue it with a new token.
CREATE TABLE IF NOT EXISTS room_issuers (
  name TEXT PRIMARY KEY, label TEXT NOT NULL, token_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, revoked_at TEXT
);
