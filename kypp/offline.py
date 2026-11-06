"""offline.py — the hosted store's local cache and outbox, for when KYPP_URL can't be reached.

`kypp sync` sends queued writes to the Worker, then pulls the operator's view of the project (POST
/v1/operator `snapshot`) into a local SQLite file. While the Worker is unreachable, `kypp recall`,
`briefing` and `show` answer from that copy and `kypp remember` is queued. The cache is a copy, never
an authority: nothing here accepts, promotes or supersedes a claim; the Worker decides all of that when
the queued write reaches it.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from . import remote
from .view import _BRIEFING_PRIORITY, compact_line

_SCOPE_NEAR = {"room": 0, "project": 0, "group": 1, "user": 2, "global": 3}
_WORD = re.compile(r"\w+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (url TEXT NOT NULL, project TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (url, project, id));
CREATE TABLE IF NOT EXISTS synced (url TEXT NOT NULL, project TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (url, project));
CREATE TABLE IF NOT EXISTS forgotten (url TEXT NOT NULL, id TEXT NOT NULL, purged INTEGER NOT NULL, at REAL NOT NULL,
  PRIMARY KEY (url, id));
CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, project TEXT NOT NULL,
  op TEXT NOT NULL, args TEXT NOT NULL, queued_at TEXT NOT NULL, sent_at TEXT, key TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> str:
    return os.environ.get("KYPP_CACHE_DB", os.path.expanduser("~/.kypp/offline.db"))


def _db() -> sqlite3.Connection:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    # The copy holds the owner's user-scope memory: keep it, and SQLite's journal, this OS user's only.
    old = os.umask(0o077)
    try:
        db = sqlite3.connect(path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    db.executescript(_SCHEMA)
    # A cache made before writes carried keys gains the column; push gives each row its key. The check
    # and the ALTER share one write transaction, so two CLIs opening an old cache can't both add it.
    db.execute("BEGIN IMMEDIATE")
    try:
        if "key" not in {r[1] for r in db.execute("PRAGMA table_info(outbox)")}:
            db.execute("ALTER TABLE outbox ADD COLUMN key TEXT")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return db


def _url() -> str:
    return remote.remote_url().rstrip("/")


def _synced_at(db: sqlite3.Connection, project: str) -> str | None:
    row = db.execute("SELECT at FROM synced WHERE url = ? AND project = ?", (_url(), project)).fetchone()
    return row[0] if row else None


def _cached(db: sqlite3.Connection, project: str) -> list[dict]:
    rows = db.execute("SELECT body FROM claims WHERE url = ? AND project = ?", (_url(), project))
    return [json.loads(b) for (b,) in rows]


def _queued(db: sqlite3.Connection, project: str) -> list[dict]:
    rows = db.execute("SELECT op, args, queued_at FROM outbox WHERE url = ? AND project = ? ORDER BY seq",
                      (_url(), project))
    return [{"op": op, "queued_at": at, **json.loads(a)} for op, a, at in rows]


def open_cache(project: str | None, cause: remote.Unreachable) -> sqlite3.Connection:
    """The cache for this Worker and project (None: any synced project), announced on stderr. With no
    sync yet there is nothing to answer from, so the original can't-reach error stands."""
    db = _db()
    at = _synced_at(db, project) if project is not None else db.execute(
        "SELECT max(at) FROM synced WHERE url = ?", (_url(),)).fetchone()[0]
    if at is None:
        raise SystemExit(f"{cause.code}\nkypp: no offline copy of {project or 'any project'!r} yet; "
                         "run `kypp sync` while online")
    print(f"kypp: offline ({cause.code.removeprefix('kypp: ')}); answering from the copy synced {at}",
          file=sys.stderr)
    return db


def _line(c: dict) -> str:
    return compact_line(SimpleNamespace(grounding=[], **{k: c.get(k) for k in
                                                         ("id", "type", "subject", "content", "status",
                                                          "authority", "confidence")}))


def _ordered(claims: list[dict], first=lambda c: 0) -> list[dict]:
    """Hosted recall's tie-breaks after `first`: accepted, nearer level, confidence, newest, then id.
    Stable sorts, last key first."""
    out = sorted(claims, key=lambda c: c["id"])
    out.sort(key=lambda c: c["updated_at"], reverse=True)
    out.sort(key=lambda c: (first(c), c["status"] != "accepted", _SCOPE_NEAR.get(c["scope"], 3), -c["confidence"]))
    return out


def recall(db: sqlite3.Connection, project: str, query: str, types: list[str] | None, candidates: bool,
           limit: int) -> str:
    """Word-match over the cached copy. Rougher than the Worker's FTS5 + rerank, which is fine for a
    fallback: it ranks by how many query words a claim holds, then the hosted tie-breaks."""
    words = {w.casefold() for w in _WORD.findall(query)}
    if query.strip() and not words:
        return "(no matching memory)"
    def matched(c: dict) -> int:
        return len(words & {w.casefold() for w in _WORD.findall(f"{c['subject']} {c['content']}")})

    hits = [c for c in _cached(db, project)
            if (candidates or c["status"] == "accepted") and (not types or c["type"] in types)
            and (not words or matched(c))]
    lines = [_line(c) for c in _ordered(hits, lambda c: -matched(c))]
    lines += [f"(queued) [{q.get('type', 'fact')}] {q['subject']} — {q['content']}" for q in _queued(db, project)
              if q["op"] == "remember" and (not types or q.get("type", "fact") in types)
              and (not words or matched(q))]
    return "\n".join(lines[:max(limit, 0)]) or "(no matching memory)"


def briefing(db: sqlite3.Connection, project: str, limit: int) -> str:
    claims = _ordered([c for c in _cached(db, project) if c["status"] == "accepted"],
                      lambda c: _BRIEFING_PRIORITY.get(c["type"], 3))
    return "\n".join(_line(c) for c in claims[:limit]) or "(no accepted memory yet)"


def show(db: sqlite3.Connection, handle: str) -> dict:
    """A handle from any synced project of this Worker, as online `show` isn't project-scoped either."""
    rows = db.execute("SELECT id, body FROM claims WHERE url = ?", (_url(),))
    found = list({i: json.loads(b) for i, b in rows if i.startswith(handle)}.values()) if len(handle) >= 8 else []
    if len(found) != 1:
        raise SystemExit(f"kypp: no single cached claim {handle!r} ({len(found)} match)")
    return found[0]


def queue(project: str, op: str, args: dict, cause: remote.Unreachable) -> int:
    """Hold a write for the next `kypp sync`. Returns how many writes are now waiting. Only a project
    synced before is queued: online, an unregistered one would be refused on the spot."""
    if op not in _QUEUEABLE:
        raise ValueError(f"{op} is never queued")
    db = _db()
    if _synced_at(db, project) is None:
        raise SystemExit(f"{cause.code}\nkypp: not queued: no offline copy of {project!r}, so it isn't known "
                         "to be a hosted project; run `kypp sync` while online first")
    with db:
        db.execute("INSERT INTO outbox(url, project, op, args, queued_at) VALUES(?,?,?,?,?)",
                   (_url(), project, op, json.dumps(args), _now()))
    return db.execute("SELECT count(*) FROM outbox WHERE url = ?", (_url(),)).fetchone()[0]


def _fetch(project: str) -> list[dict]:
    """The Worker's current view of a project, every page. The cursor must move forward each page."""
    claims, after = [], ""
    while True:
        page = remote.call("snapshot", project=project, after=after or None)
        claims += page["claims"]
        if not page.get("next"):
            return claims
        if page["next"] <= after:
            raise SystemExit(f"kypp: the Worker's snapshot cursor did not advance past {after!r}; not caching")
        after = page["next"]


# Only an agent-authority `remember` is ever queued; replay rebuilds its arguments from these keys, so a
# row edited on disk can't become a human or privileged operator write.
_QUEUEABLE = {"remember": ("type", "subject", "content", "scope", "project", "confidence", "accept", "verify",
                           "agent")}


def forget(claim_id: str, purged: bool) -> None:
    """Drop a claim the Worker just purged or rejected from every cached project. A purge (for leaked
    secrets) also leaves a tombstone; a rejected claim can come back through `kypp verify`, so it doesn't."""
    if remote.remote_url() and os.path.exists(_path()):
        db = _db()
        with db:
            # The tombstone outlives a pull that fetched the claim before the purge and writes after it.
            # A reject's mark only fences pulls already in flight: a later snapshot may hold the claim again
            # after `kypp verify` revives it. A purge's mark is permanent.
            db.execute("INSERT INTO forgotten(url, id, purged, at) VALUES(?, ?, ?, ?) ON CONFLICT(url, id) DO UPDATE"
                       " SET purged = max(purged, excluded.purged), at = excluded.at",
                       (_url(), claim_id, int(purged), time.time()))
            db.execute("DELETE FROM claims WHERE url = ? AND id = ?", (_url(), claim_id))


def _keyed() -> bool:
    """Whether the Worker honours idempotency keys. One deployed before them ignores `key`."""
    try:
        return remote.call("capabilities").get("idempotent_writes") is True
    except remote.Unreachable:
        raise
    except SystemExit as e:
        if "unknown op" in str(e.code):
            return False
        raise


def push(db: sqlite3.Connection) -> int:
    """Send queued writes in order. Against a Worker that honours keys, each is sent under its own
    idempotency key, so it is applied at most once however often it is sent: a write leaves the queue
    only after a 200, and any other answer (or none) leaves it, and the writes after it, for the next
    `kypp sync` to send again. A row sent without a key (to an older Worker, or by an older CLI) may or
    may not have landed, so it is never resent on its own: the operator decides it."""
    sent, keyed = 0, None
    for seq, op, raw, sent_at, key in db.execute(
            "SELECT seq, op, args, sent_at, key FROM outbox WHERE url = ? ORDER BY seq", (_url(),)).fetchall():
        if op not in _QUEUEABLE:
            raise SystemExit(f"kypp: queued write #{seq} is `{op}`, which is never queued; "
                             f"drop it with `kypp sync --drop {seq}`")
        stored = json.loads(raw)
        args = {k: stored.get(k) for k in _QUEUEABLE[op]}
        # A row keeps a key only while every send of it went to a Worker that honours keys: it is fixed on
        # disk before such a send and cleared before any other, so only a keyed row is resent on its own.
        if keyed is None:
            keyed = _keyed()
        if sent_at and not (key and keyed):
            why = "and the Worker no longer honours idempotency keys" if key else "without an idempotency key"
            raise SystemExit(_unsure(seq, args, f"it was sent at {sent_at} {why} and no 200 came back"))
        key = (key or secrets.token_hex(16)) if keyed else None
        with db:
            db.execute("UPDATE outbox SET sent_at = ?, key = ? WHERE seq = ?", (_now(), key, seq))
        try:
            remote.call(op, key=key, **args)
        except remote.Unreachable:
            with db:  # this attempt never connected; an earlier send may still have landed, so keep its mark
                db.execute("UPDATE outbox SET sent_at = ? WHERE seq = ?", (sent_at, seq))
            raise
        except SystemExit as e:
            if not key:
                raise SystemExit(f"{e.code}\n" + _unsure(seq, args, "the Worker (which predates idempotency keys) "
                                                               "didn't answer 200")) from None
            raise SystemExit(f"{e.code}\nkypp: queued write #{seq} ({args.get('subject')!r}) and the writes after "
                             f"it stay queued; the next `kypp sync` sends it again under the same key, so it lands "
                             f"at most once. If the Worker refused it, fix the cause or run "
                             f"`kypp sync --drop {seq}`.") from None
        with db:
            db.execute("DELETE FROM outbox WHERE seq = ?", (seq,))
        sent += 1
    return sent


def _unsure(seq: int, args: dict, why: str) -> str:
    return (f"kypp: queued write #{seq} ({args.get('subject')!r}) may already be in the hosted store: {why}. "
            f"It and the writes after it stay queued. Check with `kypp recall --candidates`, then run "
            f"`kypp sync --drop {seq}` if it's there or `kypp sync --resend {seq}` if it isn't.")


def pull(db: sqlite3.Connection, project: str) -> int:
    """Replace this project's copy with the Worker's current view, all pages or nothing."""
    started = time.time()
    claims = _fetch(project)
    with db:
        db.execute("DELETE FROM claims WHERE url = ? AND project = ?", (_url(), project))
        gone = {i for (i,) in db.execute("SELECT id FROM forgotten WHERE url = ? AND (purged = 1 OR at >= ?)",
                                         (_url(), started))}
        db.executemany("INSERT INTO claims(url, project, id, body) VALUES(?,?,?,?)",
                       [(_url(), project, c["id"], json.dumps(c)) for c in claims if c["id"] not in gone])
        db.execute("INSERT OR REPLACE INTO synced(url, project, at) VALUES(?,?,?)", (_url(), project, _now()))
    return len(claims)


def sync_main():
    ap = argparse.ArgumentParser(
        description="send writes queued while offline to the hosted store (KYPP_URL), then pull a local "
                    "copy of the project so recall/briefing/show work when the Worker can't be reached")
    ap.add_argument("--project", default=remote.default_project())
    ap.add_argument("--drop", type=int, action="append", default=[], metavar="N",
                    help="discard queued write #N without sending it (it reached the store, or it was refused)")
    ap.add_argument("--resend", type=int, action="append", default=[], metavar="N",
                    help="send queued write #N again (one sent without an idempotency key): you checked that the "
                         "hosted store doesn't have it")
    args = ap.parse_args()
    if not remote.remote_url():
        raise SystemExit("kypp sync: set KYPP_URL; the local store needs no sync")
    db = _db()
    # One replay at a time: two syncs sending the same unsent rows would each insert them.
    lock = open(_path() + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("kypp sync: another `kypp sync` is running against this cache") from None
    for seq in args.drop:
        row = db.execute("SELECT op, args FROM outbox WHERE url = ? AND seq = ?", (_url(), seq)).fetchone()
        if not row:
            raise SystemExit(f"kypp sync: no queued write #{seq}")
        with db:
            db.execute("DELETE FROM outbox WHERE seq = ?", (seq,))
        print(f"dropped queued write #{seq}: {row[0]} {json.loads(row[1]).get('subject')!r}")
    for seq in args.resend:
        with db:
            if not db.execute("UPDATE outbox SET sent_at = NULL WHERE url = ? AND seq = ?", (_url(), seq)).rowcount:
                raise SystemExit(f"kypp sync: no queued write #{seq}")
    try:
        sent, failed = push(db), None
    except remote.Unreachable as e:
        n = db.execute("SELECT count(*) FROM outbox WHERE url = ?", (_url(),)).fetchone()[0]
        raise SystemExit(f"{e.code}\nkypp: still offline; {n} write(s) stay queued") from None
    except SystemExit as e:
        sent, failed = None, e
    # A stuck write must not also freeze the copy: refresh it, then report the failure.
    got = pull(db, args.project)
    if failed:
        raise SystemExit(f"{failed.code}\nkypp: cached {got} claim(s) of {args.project!r} anyway")
    print(f"sent {sent} queued write(s); cached {got} claim(s) of {args.project!r}")
