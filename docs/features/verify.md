---
id: verify
paths:
  - "kypp/verify.py"
entry:
  - "kypp/verify.py"
tests:
  - "tests/test_verify_timeout.py"
---
# Verify

`kypp verify` re-runs each claim's `verify` shell command. A pass marks the claim accepted
and verified; a failure rejects it, and it revives if a later run passes.

## Gotchas
- Verify commands are an operator capability, never minted by agents. Never run it
  against a store whose verify commands you didn't vet.
