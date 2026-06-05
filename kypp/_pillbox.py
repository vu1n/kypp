"""Default §0-log source: pillbox session logs.

kypp consumes the §0 event format; pillbox is the default producer. Its logs live under
`<pillbox-state>/sessions/<id>/log.jsonl` for BOTH the global pillbox (`~/.pillbox/global/`) and each
project pillbox (`~/.pillbox/projects/<key>/`) — note the global-vs-project levels differ, so a single
`~/.pillbox/*/sessions/*` glob silently MISSES project sessions. Single-sourced here so the sweep,
batch, and by-id resolver all agree; override the source via the CLIs' `--logs`.
"""
from __future__ import annotations

import glob
import os

# global is one level under ~/.pillbox; project pillboxes are two (projects/<key>/). Both needed.
_SESSION_DIRS = ("~/.pillbox/global/sessions", "~/.pillbox/projects/*/sessions")


def session_logs(override: str | None = None) -> list[str]:
    """All §0 session logs from the default pillbox source (global + projects), or from `override`
    (a single glob the caller supplies via --logs)."""
    pats = (override,) if override else tuple(f"{d}/*/log.jsonl" for d in _SESSION_DIRS)
    return [p for pat in pats for p in glob.glob(os.path.expanduser(pat))]


def find_session_log(sid: str) -> str | None:
    """Resolve a session id (or prefix) to its log across global + project pillboxes."""
    for d in _SESSION_DIRS:
        hits = sorted(glob.glob(os.path.expanduser(f"{d}/{sid}*/log.jsonl")))
        if hits:
            return hits[0]
    return None
