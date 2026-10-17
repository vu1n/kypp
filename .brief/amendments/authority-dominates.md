# Amendment proposal: authority-dominates

**Decision:** doc://kypp/authority-order@0007#authority-dominates
**Proposed by:** Codex, at the user's request. **Date:** 2026-10-17.

## What should change

Preserve authority ordering and privileged routes. Authority constrains accepted-answer replacement;
eligible same-authority replacements use transactional order instead of confidence/source-count/time.
Record acceptance reason separately. MCP corrections become candidates; explicit admission does not
raise authority. Revisions, fenced checks and atomic correction replace unrestricted status writes.
The exact text is in the decision diff.

## Why

The old ranking and timestamp exceptions obscure proposal, publication, endorsement and evidence.
The user requested a simpler state machine. Same-millisecond stale-plan collisions and non-atomic
local arbitration were reproduced.

## Code that needs it

Future operator/MCP commands, arbitration, stores and verification. PR #33 should implement commands
rather than freeze arbitrary set_status. This PR changes no runtime writer.

## Impact / risk

Reported corrections no longer self-accept. Legacy acceptance stays unknown when evidence is absent.
All writers must upgrade before revision-only CAS. Operator/sync credentials stay distinct; sync is
still future work. No privileged claim is written by this PR.
