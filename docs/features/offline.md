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
- Only `remote.Unreachable` (no connection was made) falls back. urllib wraps a failure while sending
  in `URLError` too, so `remote.py` records whether `connect()` finished (`_conn.made`); after it, a
  failure is ambiguous and never falls back.
- Only a 4xx (not 408) is `remote.Refused`. A 5xx or a gateway's 502/504 can follow a committed write, so
  push treats it like a lost answer and re-checks the snapshot before sending again. An error answer, a read timeout or a
  connection dropped mid-request may have been applied, so they fail as before and nothing is queued.
- The cache is a copy, never an authority: nothing in `offline.py` accepts, promotes or supersedes. The
  Worker applies its own rules when a queued write arrives.
- `correct`, `reject` and `purge` are refused offline (`shell._online`): they replace or retire memory
  and must meet the Worker's current state, not a stale copy's.
- A queued write is marked sent before its request and deleted only after the Worker answered it. One
  sent before with no answer (timeout, dropped connection, crash) is re-sent only if the project's live
  claims don't already hold its subject and content. The operator `remember` has no command id yet, so a
  claim applied and then rejected or superseded before the next sync can still come back as a second row.
- `remember` is queued only for a project synced before, since online an unregistered one is refused at
  once. A write the Worker refuses stops the push but not the pull; `kypp sync --drop N` discards it.
- A re-check of an unanswered write matches the whole payload (scope, type, subject, content, verify,
  confidence clamped to [0, 1] as the Worker stores it, and accepted if `--accept`), and a refusal clears the sent mark: it was not applied.
- Replay holds an exclusive lock (`offline.db.lock`); a second `kypp sync` exits instead of re-sending.
- A successful hosted `purge` or `reject` drops that claim from every cached project (`offline.forget`).
  A purge also leaves a tombstone (`forgotten`) that every later pull skips, so a sync that fetched the
  claim before the purge can't write it back. A reject leaves none: `kypp verify` can revive the claim.
- A TLS failure is not "offline": a certificate that fails to verify may be interception, so it errors.
- Replay sends only `remember`, with its arguments rebuilt from a fixed key list (`_QUEUEABLE`), so an
  outbox row edited on disk can't become a correct, purge or other operator write.
- The cache file is created mode 600: it holds the owner's user-scope memory.
- A pull replaces the project's copy only after every page arrived; a failed pull keeps the old copy.
- Offline recall is a word match ranked by matched words, then the hosted tie-breaks. It has no FTS5,
  pointer hits or rerank, so it is a fallback, not the same answer.
