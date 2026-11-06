---
id: offline
paths:
  - "kypp/offline.py"
  - "tests/test_offline.py"
entry:
  - "kypp/offline.py"
tests:
  - "tests/test_offline.py"
  - "worker/test/operator.test.ts"
---
# Offline cache and sync

With `KYPP_URL` set, `kypp sync` sends writes queued while offline to the Worker, then pulls the
operator's view of the project (`POST /v1/operator` `snapshot`, 500 claims a page) into
`~/.kypp/offline.db` (`KYPP_CACHE_DB`). When the Worker can't be reached, `kypp recall`, `briefing` and
`show` answer from that copy and say so on stderr, and `kypp remember` is queued.

## Gotchas
- Only `remote.Unreachable` (no connection was made) falls back. An error answer, a read timeout or a
  connection dropped mid-request may have been applied, so they fail as before and nothing is queued.
- The cache is a copy, never an authority: nothing in `offline.py` accepts, promotes or supersedes. The
  Worker applies its own rules when a queued write arrives.
- `correct`, `reject` and `purge` are refused offline (`shell._online`): they replace or retire memory
  and must meet the Worker's current state, not a stale copy's.
- A queued write is deleted only after the Worker answered it, so a crash between that answer and the
  delete re-sends it once. The operator `remember` has no command id yet, so that re-send is a second row.
- A pull replaces the project's copy only after every page arrived; a failed pull keeps the old copy.
- Offline recall is a word match ranked by matched words, then the hosted tie-breaks. It has no FTS5,
  pointer hits or rerank, so it is a fallback, not the same answer.
