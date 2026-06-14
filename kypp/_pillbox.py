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
import re

# global is one level under ~/.pillbox; project pillboxes are two (projects/<key>/). Both needed.
_SESSION_DIRS = ("~/.pillbox/global/sessions", "~/.pillbox/projects/*/sessions")

# the project is encoded in the same path grammar: global pillbox → "global"; project pillbox → its key.
_PROJECT_RE = re.compile(r"/\.pillbox/(?:(global)|projects/([^/]+))/sessions/")


def project_for_log(path: str) -> str | None:
    """Derive a session's PROJECT from its log path — each pillbox encodes it there (`global/` vs
    `projects/<dash-encoded-cwd>/`). None for foreign layouts (custom --logs globs); callers fall
    back to an explicit/env project. This is what lets `kypp sweep` multi-project a mixed store
    instead of mis-filing every session under one KYPP_PROJECT (the 359→"default" incident)."""
    m = _PROJECT_RE.search(os.path.abspath(os.path.expanduser(path)))
    return (m.group(1) or m.group(2)) if m else None


def project_for_cwd(path: str = ".") -> str:
    """Encode a working directory into the SAME project key `project_for_log` parses OUT of a pillbox
    log path — the filesystem-path-as-key scheme (`/Users/me/code/foo` → `-Users-me-code-foo`: pure
    `/`→`-`, dots preserved, e.g. real key `-Users-vuln-.pillbox-evalstore`). This is what lets the MCP
    server (live recall/claim) and `sweep` (capture from logs) file under ONE bucket per repo instead of
    split-braining. Encode + parse single-sourced here so they can't drift — a mismatch silently splits
    a repo's memory across two project namespaces."""
    return os.path.abspath(os.path.expanduser(path)).replace("/", "-")


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


if __name__ == "__main__":
    # self-test: project derivation covers both pillbox levels and refuses foreign layouts.
    assert project_for_log(os.path.expanduser("~/.pillbox/global/sessions/abc123/log.jsonl")) == "global"
    assert project_for_log("/home/x/.pillbox/projects/-Users-me-code-foo/sessions/abc/log.jsonl") \
        == "-Users-me-code-foo"
    assert project_for_log("~/.pillbox/projects/k/sessions/s/log.jsonl") == "k"  # unexpanded input ok
    assert project_for_log("/tmp/custom/sessions/abc/log.jsonl") is None, "foreign layout → None"
    # encode/parse round-trip: a cwd-derived key parses back out of its own pillbox log path (so the
    # MCP server's derived project == the key sweep files captured claims under — one bucket per repo).
    key = project_for_cwd("/Users/me/code/foo")
    assert key == "-Users-me-code-foo", key
    assert project_for_log(os.path.expanduser(f"~/.pillbox/projects/{key}/sessions/s/log.jsonl")) == key
    # dots survive the encode (pillbox keeps them — '-Users-vuln-.pillbox-evalstore' is a real on-disk key)
    assert project_for_cwd("/Users/vuln/.pillbox/evalstore") == "-Users-vuln-.pillbox-evalstore"
    print("OK — _pillbox: project derived from global + project log paths; None for foreign layouts; "
          "cwd→key encode round-trips with the log-path parse")
