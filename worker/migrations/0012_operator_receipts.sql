-- One row per operator write sent with an idempotency key: a resend of that key returns this claim
-- instead of inserting another. Kept for good, so a write queued offline can be resent at any later sync.
CREATE TABLE IF NOT EXISTS operator_receipts (
  key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, claim_id TEXT NOT NULL, created_at TEXT NOT NULL
);
