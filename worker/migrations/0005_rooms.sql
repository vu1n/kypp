-- Room lanes (memory-scope-decay §11): a chat room's bot is its own principal. A room is registered by
-- an operator-configured integration, which receives the room's bearer token once; only its digest is
-- kept. `key` is "<integration>:<id>", so two integrations can't collide. Revoking keeps the row (and
-- the lane's claims) as history.
CREATE TABLE IF NOT EXISTS rooms (
  key TEXT PRIMARY KEY, integration TEXT NOT NULL, token_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revoked_at TEXT
);
-- The registered projects a room may read and share into. Set by whoever manages the room, never by
-- the bot.
CREATE TABLE IF NOT EXISTS room_links (
  room TEXT NOT NULL, project TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (room, project)
);
