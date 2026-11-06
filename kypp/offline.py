"""offline.py — the hosted store's local cache and outbox, for when KYPP_URL can't be reached.

`kypp sync` sends queued writes to the Worker, then pulls the operator's view of the project (POST
/v1/operator `snapshot`) into a local SQLite file. While the Worker is unreachable, `kypp recall`,
`briefing` and `show` answer from that copy and `kypp remember` is queued. The cache is a copy, never
an authority: nothing here accepts, promotes or supersedes a claim; the Worker decides all of that when
the queued write reaches it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
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
CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, project TEXT NOT NULL,
  op TEXT NOT NULL, args TEXT NOT NULL, queued_at TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    path = os.environ.get("KYPP_CACHE_DB", os.path.expanduser("~/.kypp/offline.db"))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
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


def open_cache(project: str, cause: remote.Unreachable) -> sqlite3.Connection:
    """The cache for this Worker and project, announced on stderr. With no sync yet there is nothing
    to answer from, so the original can't-reach error stands."""
    db = _db()
    at = _synced_at(db, project)
    if at is None:
        raise SystemExit(f"{cause.code}\nkypp: no offline copy of {project!r} yet; run `kypp sync` while online")
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
    lines = [_line(c) for c in _ordered(hits, lambda c: -matched(c))[:limit]]
    lines += [f"(queued) [{q.get('type', 'fact')}] {q['subject']} — {q['content']}" for q in _queued(db, project)
              if q["op"] == "remember" and (not types or q.get("type", "fact") in types)
              and (not words or matched(q))]
    return "\n".join(lines) or "(no matching memory)"


def briefing(db: sqlite3.Connection, project: str, limit: int) -> str:
    claims = _ordered([c for c in _cached(db, project) if c["status"] == "accepted"],
                      lambda c: _BRIEFING_PRIORITY.get(c["type"], 3))
    return "\n".join(_line(c) for c in claims[:limit]) or "(no accepted memory yet)"


def show(db: sqlite3.Connection, project: str, handle: str) -> dict:
    found = [c for c in _cached(db, project) if c["id"].startswith(handle)] if len(handle) >= 8 else []
    if len(found) != 1:
        raise SystemExit(f"kypp: no single cached claim {handle!r} ({len(found)} match)")
    return found[0]


def queue(project: str, op: str, args: dict) -> int:
    """Hold a write for the next `kypp sync`. Returns how many writes are now waiting."""
    db = _db()
    with db:
        db.execute("INSERT INTO outbox(url, project, op, args, queued_at) VALUES(?,?,?,?,?)",
                   (_url(), project, op, json.dumps(args), _now()))
    return db.execute("SELECT count(*) FROM outbox WHERE url = ?", (_url(),)).fetchone()[0]


def push(db: sqlite3.Connection) -> int:
    """Send queued writes in order. Each leaves the queue only after the Worker answered it, so a
    crash re-sends instead of dropping. Any failure stops the push with the rest still queued."""
    sent = 0
    for seq, op, args in db.execute("SELECT seq, op, args FROM outbox WHERE url = ? ORDER BY seq",
                                    (_url(),)).fetchall():
        try:
            remote.call(op, **json.loads(args))
        except remote.Unreachable:
            raise
        except SystemExit as e:
            left = db.execute("SELECT count(*) FROM outbox WHERE url = ?", (_url(),)).fetchone()[0]
            raise SystemExit(f"{e.code}\nkypp: the Worker refused a queued `{op}`; it and {left - 1} after it "
                             "stay queued") from None
        with db:
            db.execute("DELETE FROM outbox WHERE seq = ?", (seq,))
        sent += 1
    return sent


def pull(db: sqlite3.Connection, project: str) -> int:
    """Replace this project's copy with the Worker's current view, all pages or nothing."""
    claims, after = [], ""
    while True:
        page = remote.call("snapshot", project=project, after=after or None)
        claims += page["claims"]
        if not page.get("next"):
            break
        after = page["next"]
    with db:
        db.execute("DELETE FROM claims WHERE url = ? AND project = ?", (_url(), project))
        db.executemany("INSERT INTO claims(url, project, id, body) VALUES(?,?,?,?)",
                       [(_url(), project, c["id"], json.dumps(c)) for c in claims])
        db.execute("INSERT OR REPLACE INTO synced(url, project, at) VALUES(?,?,?)", (_url(), project, _now()))
    return len(claims)


def sync_main():
    ap = argparse.ArgumentParser(
        description="send writes queued while offline to the hosted store (KYPP_URL), then pull a local "
                    "copy of the project so recall/briefing/show work when the Worker can't be reached")
    ap.add_argument("--project", default=remote.default_project())
    args = ap.parse_args()
    if not remote.remote_url():
        raise SystemExit("kypp sync: set KYPP_URL; the local store needs no sync")
    db = _db()
    try:
        sent = push(db)
    except remote.Unreachable as e:
        n = db.execute("SELECT count(*) FROM outbox WHERE url = ?", (_url(),)).fetchone()[0]
        raise SystemExit(f"{e.code}\nkypp: still offline; {n} write(s) stay queued") from None
    got = pull(db, args.project)
    print(f"sent {sent} queued write(s); cached {got} claim(s) of {args.project!r}")
