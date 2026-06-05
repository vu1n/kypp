#!/usr/bin/env python3
"""shell.py — memory verbs for humans, scripts, and agents WITHOUT the MCP attached (Bash is
universal; an MCP attach isn't). Thin argparse mains over the store + view — the same compact
handle lines as the MCP tools, so the two surfaces read identically:

  kypp recall "libkrun docker"     # search → compact lines with handles
  kypp show a1b2c3d4               # dereference a handle → full claim JSON
  kypp remember "subject" "lesson" # store a claim (subject = identity key)
  kypp briefing                    # session-start digest, pitfalls first
"""
from __future__ import annotations

import argparse
import json
import os
import sys
# asdict, not mcp_server._claim_dict: the CLI is a DEBUG surface — full fidelity (project, agent,
# updated_at) is the point. _claim_dict is the trimmed agent contract; don't unify the two.
from dataclasses import asdict

from .store import store_from_env
from .view import briefing_claims, render_claims
from .vocab import HUMAN_CORRECTION_CONFIDENCE, SCOPES, TYPES


def _project_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--project", default=os.environ.get("KYPP_PROJECT", "default"))


def _session_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--session", default=os.environ.get("KYPP_SESSION"),
                    help="record that this run/session was shown the results — usage provenance "
                         "(run->claims-consumed), the foundation for outcome-driven memory quality")


def recall_main():
    ap = argparse.ArgumentParser(
        description="search shared memory — compact lines with handles (`kypp show HANDLE` expands)")
    ap.add_argument("query", nargs="*", help="search terms (empty = browse the strongest claims)")
    _project_arg(ap)
    ap.add_argument("--type", choices=TYPES, action="append", dest="types",
                    help="filter by claim type (repeatable)")
    ap.add_argument("--candidates", action="store_true", help="include unaccepted candidates")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="full claims as JSON instead of compact lines")
    _session_arg(ap)
    args = ap.parse_args()
    store = store_from_env()
    query = " ".join(args.query)
    claims = store.recall(query, project=args.project, types=args.types,
                          include_candidates=args.candidates, limit=args.limit)
    store.record_usage(args.session, claims, surface="recall", project=args.project, query=query or None)
    print(json.dumps([asdict(c) for c in claims], indent=2) if args.json else render_claims(claims))


def show_main():
    ap = argparse.ArgumentParser(description="expand a claim handle (id or 8-char prefix) to the full claim")
    ap.add_argument("handle")
    args = ap.parse_args()
    c = store_from_env().get(args.handle)
    if c is None:
        raise SystemExit(f"no claim {args.handle!r}")
    print(json.dumps(asdict(c), indent=2))


def remember_main():
    ap = argparse.ArgumentParser(
        description="store a memory claim — SUBJECT is its identity: reuse one to update/supersede")
    ap.add_argument("subject", help="short noun phrase; the dedup/supersede key")
    ap.add_argument("content", help="the durable lesson (model-agnostic — it's shared)")
    ap.add_argument("--type", choices=TYPES, default="fact")
    ap.add_argument("--scope", choices=SCOPES, default="project")
    ap.add_argument("--confidence", type=float, default=0.7)
    ap.add_argument("--accept", action="store_true", help="land accepted, not candidate")
    _project_arg(ap)
    args = ap.parse_args()
    store = store_from_env()
    cid = store.claim(args.type, args.subject, args.content, scope=args.scope,
                      project=args.project, confidence=args.confidence, accept=args.accept)
    # read the status back rather than re-spelling the store's rule (decision auto-accepts).
    status = store.get(cid).status
    print(f"{cid[:8]} [{status} {args.type}] {args.subject}")
    if status == "candidate":
        # the footgun this warns about: briefing + default recall are accepted-only, so a naively
        # seeded memory silently never surfaces.
        print("note: candidates are hidden from `kypp briefing` and default recall — "
              "use --accept, surface them with `kypp briefing --candidates` / `kypp recall "
              "--candidates`, or let corroboration accept them (`kypp consolidate`)", file=sys.stderr)


def briefing_main():
    ap = argparse.ArgumentParser(description="session-start digest: strongest accepted memory, pitfalls first")
    _project_arg(ap)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--candidates", action="store_true",
                    help="also surface this project's un-corroborated candidates (your own recent "
                         "lessons) — the same-project retry digest, not just accepted swarm truth")
    _session_arg(ap)
    args = ap.parse_args()
    store = store_from_env()
    claims = briefing_claims(store, args.project, args.limit, include_candidates=args.candidates)
    store.record_usage(args.session, claims, surface="briefing", project=args.project)
    empty = "(no memory yet)" if args.candidates else "(no accepted memory yet)"
    print(render_claims(claims, empty=empty))


def correct_main():
    ap = argparse.ArgumentParser(
        description="HUMAN correction: assert the right answer for a subject and supersede the rest — "
                    "outranks any agent claim and any amount of agent corroboration")
    ap.add_argument("subject", help="the claim's identity key (reuse the wrong claim's subject to replace it)")
    ap.add_argument("content", help="the correct answer (model-agnostic — it's shared)")
    ap.add_argument("--type", choices=TYPES, default="fact")
    ap.add_argument("--scope", choices=SCOPES, default="project")
    ap.add_argument("--confidence", type=float, default=HUMAN_CORRECTION_CONFIDENCE)
    ap.add_argument("--semantic", type=float, default=None, metavar="DIST",
                    help="also supersede differently-worded near-dups within this cosine distance "
                         "(needs KYPP_EMBED_MODEL)")
    _project_arg(ap)
    args = ap.parse_args()
    from .arbiter import consolidate
    store = store_from_env()
    cid = store.claim(args.type, args.subject, args.content, scope=args.scope, project=args.project,
                      confidence=args.confidence, authority="human")
    res = consolidate(store, project=args.project, subject=args.subject, semantic=args.semantic)
    print(f"{cid[:8]} [human {args.type}] {args.subject} — corrected; "
          f"superseded {res['superseded']} prior claim(s)")


def usage_main():
    ap = argparse.ArgumentParser(
        description="inspect memory usage provenance: which claims a run saw, or which runs saw a claim")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="the claims this run/session was shown")
    g.add_argument("--claim", help="the runs/sessions a claim was shown to (id or 8-char handle)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    store = store_from_env()
    if args.session:
        rows = store.usages_for(args.session)
    else:
        c = store.get(args.claim)  # resolve handle → full id (errors on ambiguous prefix)
        if c is None:  # distinguish "no such claim" from "claim exists but unused" (matches show_main)
            raise SystemExit(f"no claim {args.claim!r}")
        rows = store.usage_of(c.id)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("(no usage recorded)")
        return
    for r in rows:
        score = "—" if r.get("score") is None else f"{r['score']:.3f}"
        if args.session:  # claim-shaped rows
            print(f"{r['claim_id'][:8]} [{r.get('type') or '?'} {r.get('status') or '?'}] "
                  f"{r['surface']} score={score} — {r.get('subject') or '(claim gone)'}")
        else:  # consumer-shaped rows
            print(f"{r['consumer']} {r['surface']} score={score} @ {r['created_at']}")
