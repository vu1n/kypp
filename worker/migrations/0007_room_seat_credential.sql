-- A seat an operator-configured issuer made is bound to the digest of the KYPP_API_TOKENS secret it
-- was made with (memory-scope-decay §11). Removing that secret closes the seats it made,
-- as continuing a paired grant does; seats from before this column (NULL) never sign in again.
ALTER TABLE room_members ADD COLUMN issuer_digest TEXT;
