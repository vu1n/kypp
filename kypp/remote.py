"""remote.py — the CLI's hosted backend. With KYPP_URL set, `kypp recall | show | briefing | remember |
correct | reject | verify` call the Worker's POST /v1/operator with KYPP_OPERATOR_TOKEN instead of
opening the local store, so the operator's human and verified writes land in the hosted D1. Unset,
the CLI uses the local store exactly as before. Pointing KYPP_URL at a Worker run locally under
workerd (`wrangler dev`) is the documented offline fallback; the route and token are the same.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace


def remote_url() -> str | None:
    return os.environ.get("KYPP_URL") or None


def call(op: str, **args) -> dict:
    """One operator request. Any failure (no token, unreachable, a refused or bad request) exits the CLI
    with the server's message, never a traceback and never a silent fall back to the local store."""
    url = remote_url().rstrip("/") + "/v1/operator"
    token = os.environ.get("KYPP_OPERATOR_TOKEN")
    if not token:
        raise SystemExit("kypp: KYPP_URL is set but KYPP_OPERATOR_TOKEN is not")
    body = json.dumps({"op": op, **{k: v for k, v in args.items() if v is not None}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            text = json.loads(text).get("error", text)
        except (ValueError, AttributeError):
            pass
        raise SystemExit(f"kypp: {url} answered {e.code}: {text.strip()}")
    except urllib.error.URLError as e:
        raise SystemExit(f"kypp: can't reach {url}: {e.reason}")


class OperatorStore:
    """The two store calls `kypp verify` makes, against the hosted store: list a project's
    verifier-carrying claims, and record each check's verdict. The checks still run here, on the
    operator's machine."""

    def claims_with_verifier(self, project: str | None = None, subject: str | None = None) -> list:
        return [SimpleNamespace(**c) for c in call("verifiable", project=project, subject=subject)["claims"]]

    def mark_verified(self, claim_id: str, passed: bool) -> None:
        # Context: doc://kypp/authority-order@0007#authority-dominates — kypp verify's verdicts reach the hosted store only through POST /v1/operator.
        call("mark_verified", id=claim_id, passed=passed)
