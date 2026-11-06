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
- Only `remote.Unreachable` (no connection was made) falls back to the cache or queue. urllib wraps a
  failure while sending in `URLError` too, so `remote.py` records whether `connect()` finished
  (`_conn.made`); after it, a failure is ambiguous and never falls back.
- Before a push, `kypp sync` asks the Worker for `capabilities`. Against one that honours keys, each
  queued write gets an idempotency key, saved on disk before its first send and sent again on every
  retry. The Worker stores the key in `operator_receipts` in the claim's own batch and answers a repeat
  with the first claim, so a write is resent automatically after any failure (an error answer, a
  timeout, a lost reply) and still lands once; a purged claim is never written back by a late resend.
  A write leaves the queue only after a 200 and blocks the writes behind it until then; a write the
  Worker keeps refusing stays until `kypp sync --drop N`. The pull still runs, so a stuck write doesn't
  freeze the copy.
- A Worker deployed before keys answers `unknown op` to `capabilities` and ignores `key`, so the write
  goes without one. A row sent without a key (to such a Worker, or by an older CLI), or a keyed row the
  Worker no longer honours, may or may not have landed: it blocks the push until the operator checks and
  runs `--drop N` or `--resend N`. A send that never connected restores the row's earlier mark (none if
  it was never sent), so an earlier, maybe committed send stays on record.
- The cache is a copy, never an authority: nothing in `offline.py` accepts, promotes or supersedes. The
  Worker applies its own rules when a queued write arrives.
- `correct`, `reject` and `purge` are refused offline (`shell._online`): they replace or retire memory
  and must meet the Worker's current state, not a stale copy's.
- `remember` is queued only for a project synced before, since online an unregistered one is refused at
  once.
- Replay sends only `remember`, with its arguments rebuilt from a fixed key list (`_QUEUEABLE`), so an
  outbox row edited on disk can't become a correct, purge or other operator write.
- Replay holds an exclusive lock (`offline.db.lock`); a second `kypp sync` exits instead of re-sending.
- A successful hosted `purge` or `reject` drops that claim from every cached project (`offline.forget`).
  A purge also leaves a tombstone (`forgotten`) that every later pull skips, so a sync that fetched the
  claim before the purge can't write it back. A reject's mark only fences pulls already in flight
  (`forgotten.at` against the pull's start), since `kypp verify` can revive the claim.
- Offline `show` searches every synced project of the Worker, as online `show` isn't project-scoped.
- A TLS failure is not "offline": a certificate that fails to verify may be interception, so it errors.
- The cache file is created mode 600 under umask 077: it holds the owner's user-scope memory.
- A pull replaces the project's copy only after every page arrived, and the cursor must advance; a
  failed pull keeps the old copy.
- Offline recall is a word match ranked by matched words, then the hosted tie-breaks, and `--limit`
  covers queued lines too. It has no FTS5, pointer hits or rerank, so it is a fallback, not the same
  answer.
