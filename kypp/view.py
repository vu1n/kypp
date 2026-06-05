#!/usr/bin/env python3
"""view.py — the agent-facing view layer: compact claim rendering + the briefing selection.

Token economy via HANDLES — the same move code grounding makes (return a pointer, not content),
applied to claims themselves: recall/briefing emit ONE line per claim carrying an 8-char id handle,
clipped content, and the resolved code pointer; the agent dereferences only what it acts on
(MCP `expand` / `kypp show`). Verbose recall is ~11 JSON fields × ≤800-char content per hit — the
compact line cuts that ~4× and the handle recovers full fidelity on demand, so depth is paid
per-use, not per-search. Shared by mcp_server and shell so the two surfaces can't drift.
"""
from __future__ import annotations

from .store import Claim, MemoryStore

_CLIP = 240  # compact-line content budget; past it the line names the handle to expand

# What an agent needs BEFORE it has a query: known traps first, then choices made, then how-tos.
_BRIEFING_PRIORITY = {"pitfall": 0, "decision": 1, "procedure": 2}


def compact_line(c: Claim) -> str:
    """One claim → one line: `handle [type ✓conf] subject — content → code pointer`.
    ✓ accepted / ? candidate; the pointer is the FIRST resolved grounding (the agent reads the
    current code itself — we never inline file content)."""
    mark = "✓" if c.status == "accepted" else "?"
    body = " ".join(c.content.split())
    line = f"{c.id[:8]} [{c.type} {mark}{c.confidence:.1f}] {c.subject} — {body[:_CLIP]}"
    if len(body) > _CLIP:
        line += f"… (expand {c.id[:8]} for full)"
    hit = next((g for g in c.grounding if g.get("location")), None)
    if hit:
        loc = hit["location"]
        line += f" → {loc['path']}:{loc.get('line') or '?'}"
        if hit.get("status") != "grounded":
            line += " (moved)"
    elif c.grounding:  # anchors exist but none resolved against this tree — say so, don't hide it
        line += " (code ref unresolved here)"
    return line


def render_claims(claims: list[Claim], empty: str = "(no matching memory)") -> str:
    return "\n".join(compact_line(c) for c in claims) or empty


def briefing_claims(store: MemoryStore, project: str, limit: int = 12,
                    include_candidates: bool = False) -> list[Claim]:
    """The session-start digest selection: strongest claims, pitfalls/decisions/procedures first.
    Over-fetches 3× then stable-sorts by type priority so a pile of facts can't crowd out the traps
    (recall's browse mode orders by confidence/recency only). Grounding is deferred to the survivors —
    it's a ripgrep subprocess per code_ref, wasted on discards. include_candidates=True adds this
    project's un-corroborated candidates (your own recent lessons) — the same-project RETRY surface,
    vs the default accepted-only cold-start swarm-truth digest."""
    claims = store.recall("", project=project, limit=limit * 3, ground=False,
                          include_candidates=include_candidates)
    claims.sort(key=lambda c: _BRIEFING_PRIORITY.get(c.type, 3))
    return store.ground(claims[:limit])


if __name__ == "__main__":
    # self-test: compact rendering (handle, clip marker, code pointer) + briefing priority order.
    import glob
    import os
    import shutil

    from .store import MemoryStore, RipgrepResolver

    db = "/tmp/view-selftest.db"
    for f in glob.glob(db + "*"):
        try: os.remove(f)
        except OSError: pass
    repo = "/tmp/view-selftest-repo"
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(repo)
    with open(os.path.join(repo, "mod.rs"), "w") as fh:
        fh.write("fn select_backend() {}\n")

    store = MemoryStore(db, resolver=RipgrepResolver(root=repo))
    store.claim("fact", "filler", "an accepted fact", scope="project", project="p", accept=True)
    store.claim("decision", "store engine", "tursodb", scope="project", project="p")
    pid = store.claim("pitfall", "libkrun rebuild", "x" * 300, scope="project", project="p",
                      confidence=0.9, accept=True,
                      code_refs=[{"symbol": "select_backend", "path": "mod.rs"}])
    store.claim("fact", "weak candidate", "unaccepted", scope="project", project="p", confidence=0.3)

    line = compact_line(store.recall("libkrun", project="p")[0])
    assert line.startswith(pid[:8]) and "[pitfall ✓0.9]" in line, line
    assert f"(expand {pid[:8]} for full)" in line, line  # 300 chars > _CLIP → clip marker + handle
    assert "→ mod.rs:1" in line, line  # grounded pointer, not file content

    # briefing: pitfall before decision before fact; candidates excluded (browse = accepted only)
    b = briefing_claims(store, "p")
    assert [c.type for c in b] == ["pitfall", "decision", "fact"], [c.type for c in b]
    assert all(c.status == "accepted" for c in b), "default briefing is accepted-only"
    # --candidates surfaces the project's un-corroborated lessons (the retry surface)
    bc = briefing_claims(store, "p", include_candidates=True)
    assert any(c.status == "candidate" for c in bc), [c.status for c in bc]
    assert render_claims([], empty="(none)") == "(none)"

    # the handle round-trip the compact line promises: expand recovers the full claim
    full = store.get(pid[:8])
    assert full and full.content == "x" * 300 and full.grounding[0]["status"] == "grounded", full
    print(f"OK — view: compact line = handle+clip+pointer ({line[:60]}…); briefing orders "
          f"pitfall>decision>fact; store.get round-trips the handle")
