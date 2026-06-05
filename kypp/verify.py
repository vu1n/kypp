#!/usr/bin/env python3
"""verify.py — the deterministic freshness channel: re-check config/fact claims against ground truth.

The variance-FREE scoring channel (vs the noisy outcome-attribution loop): a fact/config claim — which
image tag, which flag, which endpoint — is black/white, so it can carry a `verify` shell command (exit
0 = still true) that `kypp verify` runs. Pass → the claim is marked `verified` authority + accepted, so
the confirmed fact outranks any agent guess; fail → rejected (currently false, dropped from recall).
It's bidirectional and idempotent: a claim rejected last run revives the moment its check passes again,
so `kypp verify` is safe to run on a schedule (cron) like `session prune` / `sweep`.

TRUST BOUNDARY: a verify command is arbitrary shell, executed when you run `kypp verify` (same model as
pillbox `session score --cmd`). Only attach verifiers you trust, and do NOT run `kypp verify` against a
shared store whose claims — hence whose verify commands — you didn't author until the swarm trust gate
vets them. The verifier is an operator capability, not something an agent should mint unattended.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .store import MemoryStore, store_from_env


def run_check(command: str, *, cwd: str, timeout: float = 30) -> tuple[bool, str]:
    """Run one freshness check via `sh -c`; exit 0 = pass (claim still true). Returns (passed, output)
    with a combined stdout+stderr tail for the report. A timeout or spawn failure is a FAIL (can't
    confirm → not fresh), surfaced in the output — never a raise that aborts the sweep."""
    try:
        p = subprocess.run(["sh", "-c", command], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"verify timed out after {timeout}s"
    except OSError as e:
        return False, f"verify could not run: {e}"
    return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()[-500:]


def verify_project(store: MemoryStore, *, project: str | None = None, subject: str | None = None,
                   cwd: str = ".", timeout: float = 30) -> dict:
    """Run every verifier-carrying claim in `project` (any status — revival is bidirectional) and mark
    each verified (pass) or stale (fail). Returns {checked, verified, stale, results:[{id, subject,
    passed, output}]}."""
    results, verified, stale = [], 0, 0
    for c in store.claims_with_verifier(project, subject):
        passed, output = run_check(c.verify, cwd=cwd, timeout=timeout)
        store.mark_verified(c.id, passed)
        verified += passed
        stale += not passed
        results.append({"id": c.id, "subject": c.subject, "passed": passed, "output": output})
    return {"checked": len(results), "verified": verified, "stale": stale, "results": results}


def main():
    ap = argparse.ArgumentParser(
        description="re-check config/fact claims against ground truth (deterministic freshness channel)")
    ap.add_argument("--project", default=os.environ.get("KYPP_PROJECT", "default"))
    ap.add_argument("--subject", help="limit to one subject (default: every verifier-carrying claim in the project)")
    ap.add_argument("--cwd", default=os.environ.get("KYPP_REPO_ROOT", "."),
                    help="working dir the checks run in (default: KYPP_REPO_ROOT, else cwd)")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = verify_project(store_from_env(), project=args.project, subject=args.subject,
                         cwd=args.cwd, timeout=args.timeout)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"verified {res['verified']}, stale {res['stale']} of {res['checked']} checked "
          f"in project {args.project!r}")
    for r in res["results"]:
        mark = "✓ verified" if r["passed"] else "✗ STALE"
        tail = f"  | {r['output'][:120]}" if not r["passed"] and r["output"] else ""
        print(f"  {r['id'][:8]} {mark} — {r['subject']}{tail}")


if __name__ == "__main__" and len(sys.argv) > 1:
    main()
elif __name__ == "__main__":
    # self-test: a passing + a failing verifier; verified→accepted+verified, stale→rejected (dropped
    # from recall), unverified claims untouched; then a world-change revives a rejected claim.
    import glob

    db = "/tmp/kypp-verify-selftest.db"
    for f in glob.glob(db + "*"):
        try: os.remove(f)
        except OSError: pass
    store = MemoryStore(db)
    ok = store.claim("fact", "image tag", "use pillbox-runner:l7", scope="project", project="p", verify="true")
    bad = store.claim("fact", "old flag", "use --legacy", scope="project", project="p", verify="false")
    plain = store.claim("fact", "no check", "unverified note", scope="project", project="p")  # no verifier

    res = verify_project(store, project="p")
    assert res["checked"] == 2 and res["verified"] == 1 and res["stale"] == 1, res
    okc, badc = store.get(ok), store.get(bad)
    assert okc.status == "accepted" and okc.authority == "verified", (okc.status, okc.authority)
    assert badc.status == "rejected", badc.status
    subjects = {c.subject for c in store.recall("", project="p", limit=20)}
    assert "image tag" in subjects and "old flag" not in subjects, subjects  # stale dropped from recall
    assert store.get(plain).status == "candidate" and store.get(plain).authority == "agent", "unverified untouched"

    # bidirectional revival: a check whose verdict depends on the WORLD (a marker file). Fails first
    # (rejected), then the world changes (marker created) → re-check passes → revived to verified.
    marker = "/tmp/kypp-verify-marker-selftest"
    try: os.remove(marker)
    except OSError: pass
    rev = store.claim("fact", "marker present", "the marker file exists", scope="project", project="p",
                      verify=f"test -f {marker}")
    assert verify_project(store, project="p", subject="marker present")["stale"] == 1
    assert store.get(rev).status == "rejected"
    open(marker, "w").close()  # world changes
    r2 = verify_project(store, project="p", subject="marker present")  # claims_with_verifier sees the rejected one
    assert r2["verified"] == 1 and store.get(rev).status == "accepted" and store.get(rev).authority == "verified", r2
    os.remove(marker)

    for f in glob.glob(db + "*"):
        try: os.remove(f)
        except OSError: pass
    print("OK — verify: pass→verified+accepted, fail→rejected (dropped from recall); unverified claims "
          "untouched; bidirectional revive (rejected claim re-checked after a world-change → verified)")
