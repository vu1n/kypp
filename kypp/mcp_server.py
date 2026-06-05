#!/usr/bin/env python3
"""mcp_server.py — the swarm-memory engine as a single MCP server (observe / claim / recall /
expand / briefing + the spec's decide / remember_procedure conveniences).

The ONE optional MCP an agent attaches (per swarm-memory-mcp-server-spec): a thin semantic layer over
the store — the product is memory governance, not the transport. The server is bound to one project
and one db file; many agents/pillboxes run their own server against the SAME db (tursodb concurrent
writes). Code grounding is wired by default via RipgrepResolver, so recall returns live code pointers
with zero setup; an embedder (vector recall) and a canopy/AST resolver drop in behind store's seams.

The tools are thin closures over the store — transport + serialization (compact handle lines via
view.py by default; `_claim_dict` JSON behind verbose/expand), nothing more. Tool signatures use
vocab's Literal types so the closed sets land in the tool JSON schema. `build_mcp` imports the MCP
SDK lazily, so this module imports and self-tests without the SDK; only serving needs it.
"""
from __future__ import annotations

import argparse
import os

from .arbiter import consolidate as _consolidate
from .arbiter import resolve_conflicts as _resolve_conflicts
from .store import Claim, MemoryStore, RipgrepResolver, store_from_env
from .view import briefing_claims, render_claims
from .vocab import HUMAN_CORRECTION_CONFIDENCE, ClaimType, Scope


def _claim_dict(c: Claim) -> dict:
    """Claim → the JSON an agent consumes. Includes resolved code `grounding` (live pointers) and the
    `low_confidence` flag the spec wants surfaced."""
    return {"id": c.id, "type": c.type, "subject": c.subject, "content": c.content, "scope": c.scope,
            "status": c.status, "authority": c.authority, "confidence": c.confidence,
            "source_ids": c.source_ids, "code_refs": c.code_refs, "grounding": c.grounding,
            "low_confidence": c.low_confidence}


def build_mcp(store: MemoryStore, project: str, *, name: str = "kypp",
              http: tuple[str, int] | None = None, consumer: str | None = None):
    """Register the engine's tools on a FastMCP server bound to `store`/`project`. Imports the MCP SDK
    lazily so the module stays importable (and testable) without it. `http=(host, port)` serves over
    streamable-http; None → stdio. The tool docstrings/type hints below are the agent-facing contract.

    HTTP binding + transport security MUST be set at construction — FastMCP freezes the DNS-rebinding
    allowed-hosts then, so a post-hoc settings mutation can't expose a non-localhost host. We disable
    rebinding protection: kypp is a trusted local-network infra service attached to by host IP/hostname
    (e.g. a sandboxed agent via host.docker.internal), NOT a browser-facing endpoint; the bind address
    (--host, default 127.0.0.1) is the real access control."""
    from mcp.server.fastmcp import FastMCP

    if http:
        from mcp.server.transport_security import TransportSecuritySettings
        host, port = http
        mcp = FastMCP(name, host=host, port=port,
                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    else:
        mcp = FastMCP(name)

    @mcp.tool()
    def observe(content: str, actor: str = "agent", source: str = "", scope: Scope = "project") -> str:
        """Record a raw observation — an append-only signal you're not yet sure is durable (a finding,
        an error seen, a choice made in passing). Optional: `claim` works without it. Returns the
        observation id; pass it as a claim's source_id to attribute provenance."""
        return store.observe(actor=actor, content=content, scope=scope, project=project, source=source or None)

    @mcp.tool()
    def claim(subject: str, content: str, type: ClaimType = "fact", confidence: float = 0.7,
              scope: Scope = "project", source_ids: list[str] | None = None,
              code_refs: list[dict] | None = None) -> str:
        """Record a durable memory. `subject` (short noun phrase) is the claim's IDENTITY: write under
        an EXISTING subject to update/correct it (consolidation keeps the strongest version); a new
        subject creates a new memory. Memory is shared across the swarm — keep content MODEL-AGNOSTIC
        (no model names; the store does not strip them on this path). Anchor to code via code_refs
        [{symbol,path,query}] when it concerns specific code. Returns the claim id."""
        return store.claim(type, subject, content, scope=scope, project=project,
                           confidence=confidence, source_ids=source_ids, code_refs=code_refs)

    @mcp.tool()
    def recall(query: str, scope: Scope | None = None, types: list[ClaimType] | None = None,
               include_candidates: bool = False, limit: int = 10,
               verbose: bool = False) -> str | list[dict]:
        """Search shared memory (semantic if an embedder is wired, else keyword). Returns one compact
        line per hit — `handle [type ✓conf] subject — content → code pointer`; pass a handle to
        `expand` for the full claim (verbose=true returns full JSON directly). Empty query browses the
        strongest claims. Prefers accepted (✓) over candidate (?), project over global, never returns
        rejected. If a recalled claim is WRONG, don't just ignore it — `claim` the correction under
        the SAME subject with higher confidence; consolidation supersedes the loser."""
        claims = store.recall(query, project=project, scope=scope, types=types,
                              include_candidates=include_candidates, limit=limit)
        store.record_usage(consumer, claims, surface="recall", project=project, query=query or None)
        return [_claim_dict(c) for c in claims] if verbose else render_claims(claims)

    @mcp.tool()
    def expand(handle: str) -> dict:
        """Dereference a recall/briefing handle (claim id or its 8-char prefix) to the full claim:
        unclipped content, provenance (source_ids), code_refs + live grounding. Also resolves
        superseded/rejected claims — a handle held from earlier context may point into history, so
        check `status` before trusting the content."""
        c = store.get(handle)
        if c is None:
            raise ValueError(f"unknown claim handle {handle!r}")
        return _claim_dict(c)

    @mcp.tool()
    def briefing(limit: int = 12) -> str:
        """Session-start digest — call ONCE before starting work, no query needed: the project's
        strongest accepted memory, known traps (pitfalls) first, then decisions and procedures.
        Lines carry handles; `expand` any you act on."""
        claims = briefing_claims(store, project, limit)
        store.record_usage(consumer, claims, surface="briefing", project=project)
        return render_claims(claims, empty="(no accepted memory yet)")

    @mcp.tool()
    def correct(subject: str, content: str, type: ClaimType = "fact") -> str:
        """Record a HUMAN correction — the authoritative right answer for a subject. Use when a human
        tells you a stored memory is WRONG and gives the correct value (e.g. the right config/tag).
        It outranks any agent claim AND any amount of agent corroboration, lands accepted, and
        supersedes prior claims on the same subject. Returns the claim id."""
        cid = store.claim(type, subject, content, scope="project", project=project,
                          confidence=HUMAN_CORRECTION_CONFIDENCE, authority="human")
        _consolidate(store, project=project, subject=subject)
        return cid

    @mcp.tool()
    def decide(subject: str, content: str, source_ids: list[str] | None = None) -> str:
        """Record an ACCEPTED decision (durable, not a candidate) — the team's chosen answer for a
        subject. Returns the claim id."""
        # type=decision auto-accepts in the store (the rule lives there, not restated here).
        return store.claim("decision", subject, content, scope="project", project=project, source_ids=source_ids)

    @mcp.tool()
    def remember_procedure(subject: str, content: str, source_ids: list[str] | None = None) -> str:
        """Record an ACCEPTED procedure — a reusable how-to. Returns the claim id."""
        return store.claim("procedure", subject, content, scope="project", project=project,
                           source_ids=source_ids, accept=True)

    @mcp.tool()
    def consolidate(subject: str = "", dry_run: bool = False, semantic: float = 0.0) -> dict:
        """Dedup memory: group claims by subject and SUPERSEDE all but the strongest (authority >
        accepted > confident > more-evidenced > newer). ALSO PROMOTES a candidate survivor to accepted
        once >= 2 independent sessions corroborate it (the swarm-truth gate). subject="" consolidates
        the whole project; dry_run=true previews the plan without writing. semantic>0 (a cosine
        distance — calibrate per embedder, ~0.25 for nomic-embed-text) ALSO merges different-subject
        near-duplicates (needs an embedder). Superseded claims are kept for history but excluded from
        recall."""
        return _consolidate(store, project=project, subject=subject or None, dry_run=dry_run,
                            semantic=semantic or None)

    @mcp.tool()
    def resolve_conflicts(subject: str) -> dict:
        """Show a subject's live claims grouped by status, with the recommended survivor (read-only;
        apply the recommendation with consolidate)."""
        return _resolve_conflicts(store, subject, project=project)

    return mcp


def main():
    ap = argparse.ArgumentParser(description="serve the kypp memory MCP")
    ap.add_argument("--http", action="store_true",
                    help="serve over HTTP (streamable-http) so remote/sandboxed clients can ATTACH "
                         "to one shared server; default is stdio (client spawns the server itself)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7077)
    args = ap.parse_args()

    http = (args.host, args.port) if args.http else None
    mcp = build_mcp(store_from_env(), os.environ.get("KYPP_PROJECT", "default"), http=http,
                    consumer=os.environ.get("KYPP_SESSION"))
    mcp.run(transport="streamable-http" if args.http else "stdio")  # http = the attach surface


if __name__ == "__main__" and os.environ.get("KYPP_MCP_SERVE"):
    main()
elif __name__ == "__main__":
    # self-test: the store + _claim_dict serialization (the MCP layer is thin closures over these),
    # no SDK needed. Set KYPP_MCP_SERVE=1 to run the stdio server instead.
    import glob
    import shutil

    db = "/tmp/kypp-mcp-selftest.db"
    for f in glob.glob(db + "*"):
        try: os.remove(f)
        except OSError: pass
    repo = "/tmp/kypp-mcp-selftest-repo"
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(os.path.join(repo, "src", "sandbox"))
    with open(os.path.join(repo, "src", "sandbox", "mod.rs"), "w") as fh:
        fh.write("fn select_backend(cfg: &Cfg) -> Backend {\n    Backend::Libkrun\n}\n")

    store = MemoryStore(db, resolver=RipgrepResolver(root=repo))

    oid = store.observe(actor="agent", content="agent hit a silent docker fallback", scope="project", project="pillbox")
    # confidence 0.9: recall breaks accepted-status ties by confidence then RECENCY — the pitfall is
    # seeded first, so without the higher confidence the newer procedure would outrank it.
    store.claim("pitfall", "libkrun rebuild",
                "rebuild with --features libkrun + re-codesign or it falls back to docker",
                scope="project", project="pillbox", source_ids=[oid], confidence=0.9,
                code_refs=[{"symbol": "select_backend", "path": "src/sandbox/mod.rs"}], accept=True)
    # the presets the decide / remember_procedure tools apply: type=decision auto-accepts; procedure
    # is accepted via accept=True. Assert both land accepted (the tool closures rely on this).
    store.claim("decision", "store engine", "tursodb embedded — concurrent writes + portability",
                scope="project", project="pillbox")
    store.claim("procedure", "rebuild for libkrun", "cargo build --features libkrun; re-codesign",
                scope="project", project="pillbox", accept=True)

    hits = [_claim_dict(c) for c in store.recall("libkrun rebuild docker fallback", project="pillbox")]
    top = hits[0]
    assert top["subject"] == "libkrun rebuild", hits
    assert set(top) == {"id", "type", "subject", "content", "scope", "status", "authority",
                        "confidence", "source_ids", "code_refs", "grounding", "low_confidence"}, set(top)
    assert top["status"] == "accepted" and top["source_ids"] == [oid]
    assert top["grounding"] and top["grounding"][0]["status"] == "grounded" \
        and top["grounding"][0]["location"]["line"] == 1, top["grounding"]
    decided = [_claim_dict(c) for c in store.recall("store engine tursodb", project="pillbox")]
    assert any(h["type"] == "decision" and h["status"] == "accepted" for h in decided), decided
    proc = [_claim_dict(c) for c in store.recall("rebuild libkrun procedure", project="pillbox", types=["procedure"])]
    assert any(h["type"] == "procedure" and h["status"] == "accepted" for h in proc), proc

    # the recall/briefing→expand handle loop the tools wrap: compact lines carry the 8-char handle,
    # expand (store.get) recovers the full claim
    compact = render_claims(store.recall("libkrun rebuild docker fallback", project="pillbox"))
    assert top["id"][:8] in compact and "→ src/sandbox/mod.rs:1" in compact, compact
    assert _claim_dict(store.get(top["id"][:8])) == top
    digest = render_claims(briefing_claims(store, "pillbox"), empty="(no accepted memory yet)")
    assert digest.splitlines()[0].split()[1] == "[pitfall", digest  # traps lead the digest

    note = ""
    try:
        srv = build_mcp(store, "pillbox")
        tm = getattr(srv, "_tool_manager", None)
        names = sorted(getattr(tm, "_tools", {})) if tm else []
        note = f"; mcp server built (tools: {names or 'registered'})"
    except ImportError:
        note = "; mcp SDK not installed — server build skipped (store + _claim_dict verified)"

    print(f"OK — mcp wrapper: tools are thin closures over the store; recall serializes via "
          f"_claim_dict with code grounding + status governance{note}")
