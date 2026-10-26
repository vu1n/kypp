---
id: capture
paths:
  - "kypp/wire.py"
  - "kypp/autocapture.py"
  - "kypp/_pillbox.py"
entry:
  - "kypp/wire.py"
  - "kypp/autocapture.py"
---
# Capture and sweep

`kypp capture` turns one §0 session log into observations, plus claims when run with
`--distill`. `kypp sweep` captures every completed pillbox session, idempotently.

## Gotchas
- `sweep` treats a session as complete only once its log has a terminal event (`scored`,
  `run_finished` or `run_failed`). In-flight sessions are skipped, not partially captured.
- `sweep` derives each session's project from its pillbox log path, not from
  `KYPP_PROJECT`.
