"""remote.py — the CLI's hosted backend. With KYPP_URL set, `kypp recall | show | briefing | remember |
correct | reject | verify | project` call the Worker's POST /v1/operator with KYPP_OPERATOR_TOKEN instead of
opening the local store, so the operator's human and verified writes land in the hosted D1. Unset,
the CLI uses the local store exactly as before. Pointing KYPP_URL at a Worker run locally under
workerd (`wrangler dev`) is the documented offline fallback; the route and token are the same.
"""
from __future__ import annotations

import http.client
import json
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace


def remote_url() -> str | None:
    return os.environ.get("KYPP_URL") or None


def default_project() -> str:
    """The project for hosted calls: KYPP_PROJECT, else the repo directory's name. The Worker knows
    projects by their registered names (as the MCP `X-Kypp-Project` header sends them), so the local
    store's path-derived key (`-Users-me-code-foo`) would never match one."""
    root = os.environ.get("KYPP_REPO_ROOT", ".")
    return os.environ.get("KYPP_PROJECT") or os.path.basename(os.path.abspath(os.path.expanduser(root)))


class Unreachable(SystemExit):
    """The Worker could not be reached at all: the request never got a connection (refused, DNS,
    connect timeout), so nothing was applied. Only this failure may fall back to the offline cache
    or queue; an answer from the Worker, even an error, and a connection lost mid-request, may not."""


class Refused(SystemExit):
    """The Worker answered 4xx: it saw the request and did not apply it."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Why: urllib re-sends every header but Content-* on a redirect, to any host, so following one
    # would hand the operator token (and the verify commands we then run) to wherever it points.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Whether this thread's current request got a connection (TCP, and TLS for https). urllib wraps a
# failure while *sending* in URLError too, and by then the Worker may have the request.
_conn = threading.local()


class _HTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _conn.made = True


class _HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _conn.made = True


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


_opener = urllib.request.build_opener(_NoRedirect, _HTTPHandler, _HTTPSHandler)


def call(op: str, **args) -> dict:
    """One operator request. Any failure (no token, unreachable, a refused or bad request) exits the CLI
    with the server's message, never a traceback and never a silent fall back to the local store."""
    url = remote_url().rstrip("/") + "/v1/operator"
    token = os.environ.get("KYPP_OPERATOR_TOKEN")
    if not token:
        raise SystemExit("kypp: KYPP_URL is set but KYPP_OPERATOR_TOKEN is not")
    parts = urllib.parse.urlsplit(url)
    # Why: the token grants human authority and the answers carry shell commands `kypp verify` runs,
    # so plain http is allowed only to this machine (the `wrangler dev` fallback).
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")):
        raise SystemExit(f"kypp: KYPP_URL must be https:// (or http:// to localhost), got {remote_url()!r}")
    body = json.dumps({"op": op, **{k: v for k, v in args.items() if v is not None}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    _conn.made = False
    try:
        with _opener.open(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            text = json.loads(text).get("error", text)
        except (ValueError, AttributeError):
            pass
        msg = f"kypp: {url} answered {e.code}: {text.strip()}"
        # Only a 4xx is the Worker saying no. A 5xx (or a gateway's 502/504, or a 408) can follow a write
        # that was committed, so it is ambiguous, never a refusal.
        if 400 <= e.code < 500 and e.code != 408:
            raise Refused(msg)
        raise SystemExit(msg)
    except urllib.error.URLError as e:
        # A certificate that fails to verify is a possible interception, never just "offline".
        if isinstance(e.reason, ssl.SSLError):
            raise SystemExit(f"kypp: TLS to {url} failed, not falling back to the offline copy: {e.reason}")
        if _conn.made:
            raise SystemExit(f"kypp: {url} dropped the connection while sending: {e.reason}")
        raise Unreachable(f"kypp: can't reach {url}: {e.reason}")
    except TimeoutError:
        raise SystemExit(f"kypp: {url} did not answer in time")
    except ValueError:
        raise SystemExit(f"kypp: {url} answered with something other than JSON; is KYPP_URL a kypp Worker?")
    except (OSError, http.client.HTTPException) as e:
        raise SystemExit(f"kypp: {url} dropped the connection: {e}")


class OperatorStore:
    """The two store calls `kypp verify` makes, against the hosted store: list a project's
    verifier-carrying claims, and record each check's verdict. The checks still run here, on the
    operator's machine."""

    def claims_with_verifier(self, project: str | None = None, subject: str | None = None) -> list:
        return [SimpleNamespace(**c) for c in call("verifiable", project=project, subject=subject)["claims"]]

    def mark_verified(self, claim_id: str, passed: bool) -> None:
        # kypp verify's verdicts reach the hosted store only through POST /v1/operator.
        call("mark_verified", id=claim_id, passed=passed)
