-- Normalize only the earlier, unactivated claim_command_* representation.
-- Legacy memory_claims and the live-store cutover are deliberately untouched.
-- Retain every staged claim/event and original receipt while changing only storage shape.
ALTER TABLE claim_command_ids ADD COLUMN ordinal INTEGER DEFAULT 0;
ALTER TABLE claim_command_ids ADD COLUMN claim TEXT;
CREATE INDEX IF NOT EXISTS claim_command_ids_group ON claim_command_ids(group_key,ordinal);
CREATE INDEX IF NOT EXISTS claim_command_receipts_group ON claim_command_receipts(group_key);
CREATE TABLE IF NOT EXISTS claim_command_events (
  group_key TEXT NOT NULL, revision INTEGER NOT NULL, event TEXT NOT NULL,
  PRIMARY KEY(group_key,revision)
);
INSERT INTO claim_command_ids(claim_id,group_key,ordinal,claim)
SELECT json_extract(c.value,'$.id'),g.group_key,CAST(c.key AS INTEGER),c.value
FROM claim_command_groups g,json_each(g.state,'$.claims') c
WHERE json_type(g.state,'$.claims')='array'
ON CONFLICT(claim_id) DO UPDATE SET
  group_key=CASE WHEN claim_command_ids.group_key=excluded.group_key THEN excluded.group_key ELSE NULL END,
  ordinal=excluded.ordinal,claim=excluded.claim;
INSERT INTO claim_command_events(group_key,revision,event)
SELECT g.group_key,json_extract(e.value,'$.revision'),e.value
FROM claim_command_groups g,json_each(g.state,'$.events') e
WHERE json_type(g.state,'$.claims')='array';
UPDATE claim_command_groups
SET state=json_object('storage_version',2,'head',json_extract(state,'$.head'),'head_revision',json_extract(state,'$.head_revision'))
WHERE json_type(state,'$.claims')='array';
