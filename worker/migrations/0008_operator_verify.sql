-- The operator's verify command (kypp remember/correct --verify), run only by `kypp verify` on the
-- operator's machine. Written only through POST /v1/operator; MCP never sets it.
ALTER TABLE memory_claims ADD COLUMN verify TEXT;
