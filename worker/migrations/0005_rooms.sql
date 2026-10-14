-- Room lanes (memory-scope-decay §11): a chat room is its own principal. A room is registered by an
-- issuer (an operator-configured integration or a paired grant). `key` is "<integration>:<id>", so two
-- issuers can't collide. Revoking keeps the row (and the lane's claims) as history.
CREATE TABLE IF NOT EXISTS rooms (
  key TEXT PRIMARY KEY, integration TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revoked_at TEXT
);
-- The bots seated in a room, each with its own bearer token for the room's one lane. The issuer gets
-- a token once; only its digest is kept. Removing a bot revokes its member token, not the room.
CREATE TABLE IF NOT EXISTS room_members (
  room TEXT NOT NULL, member TEXT NOT NULL, token_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revoked_at TEXT,
  PRIMARY KEY (room, member)
);
-- The registered projects a room may read and share into. Set by whoever manages the room, never by
-- the bot.
CREATE TABLE IF NOT EXISTS room_links (
  room TEXT NOT NULL, project TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (room, project)
);
