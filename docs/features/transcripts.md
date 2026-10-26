---
id: transcripts
paths:
  - "kypp/transcripts.py"
  - "kypp/seed.py"
entry:
  - "kypp/transcripts.py"
  - "kypp/seed.py"
---
# Transcript seeding

`kypp seed <repo>` bootstraps memory from Claude Code and Codex transcript history, converted
into §0 events.

## Gotchas
- `seed` skips eval-contaminated sessions, because those may become eval tasks. A session
  that both seeds memory and becomes an eval task invalidates every lift measurement.
- Codex transcripts can repeat a `call_id`. Duplicates collapse, but distinct calls without
  an id must not.
