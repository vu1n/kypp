#!/usr/bin/env python3
"""arbiter.py — memory governance: consolidate near-duplicate claims, surface conflicts.

The dedup/supersede layer the dogfood proved necessary: 359 real sessions distilled to 275 claims,
many near-identical ("most criteria failed (8/8)" ×18). Per swarm-memory-mcp-server-spec milestone 3
+ the arbiter rules — group live claims by (subject, scope, project), keep the strongest, SUPERSEDE
the rest. Never deletes: superseded rows stay for history; recall already excludes them.

"Strongest" = accepted-over-candidate, then higher confidence, then more sources (evidence), then
newer. This also implements decision supersession (a newer decision on the same subject wins). Read-only `resolve_conflicts` reports a subject's claims grouped by status.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

from .s1 import HOLD_BELOW, agreement_judge
from .store import Claim, MemoryStore, project_from_env, store_from_env
from .vocab import AUTHORITY_RANK


# Context: doc://kypp/authority-order@0002#authority-dominates — rank survivors by authority first.
def _rank(c: Claim) -> tuple:
    """Sort key, higher = stronger survivor: AUTHORITY first (a human correction outranks any agent
    claim and any amount of agent corroboration), then accepted, confidence, evidence, recency."""
    return (AUTHORITY_RANK.get(c.authority, 0), c.status == "accepted", c.confidence or 0,
            len(c.source_ids), c.updated_at)


def _corroboration(members: list[Claim]) -> int:
    """Independent backing for a subject = distinct source observations across its claims. Each distill
    run files ONE observation, so this counts the SESSIONS that landed the subject — the multiplayer
    acceptance signal (one agent's claim is a guess; many sessions agreeing make it truth)."""
    return len({s for m in members for s in m.source_ids})


def _survivor(members: list[Claim]) -> Claim:
    """The strongest claim in a group — THE one selection, shared by supersede (_plan_groups keeps it,
    drops the rest) and promote (the corroboration pass accepts it). Single-sourced so the two passes
    can't pick different winners as _rank evolves."""
    return max(members, key=_rank)


def _updates(members: list[Claim], survivor: Claim) -> list[Claim]:
    """Candidates written after an ACCEPTED survivor at its authority: pending updates to that answer.
    They outrank nothing yet (accepted beats candidate), so without this the old answer would
    supersede its own correction. They wait, and replace it once they corroborate."""
    if survivor.status != "accepted":
        return []
    return [m for m in members if m.status == "candidate" and m.authority == survivor.authority
            and m.updated_at > survivor.updated_at]


def _plan_groups(groups: list[list[Claim]], keep_updates: bool = True) -> list[dict]:
    """For each group of >1, keep the strongest (by _rank) and, for exact-subject groups, any pending
    updates to it; supersede the rest."""
    plan = []
    for members in groups:
        if len(members) < 2:
            continue
        survivor = _survivor(members)
        keep = {survivor.id} | ({u.id for u in _updates(members, survivor)} if keep_updates else set())
        dropped = [m.id for m in members if m.id not in keep]
        if dropped:
            plan.append({"subject": survivor.subject, "survivor": survivor.id, "superseded": dropped})
    return plan


def _semantic_clusters(pairs: list[tuple[str, str]], alive: dict[str, Claim]) -> list[list[Claim]]:
    """Union-find the near-dup pairs (restricted to still-alive ids) into clusters of >1."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        if a in alive and b in alive:
            parent[find(a)] = find(b)
    clusters: dict[str, list[Claim]] = defaultdict(list)
    for cid in parent:
        clusters[find(cid)].append(alive[cid])
    return [c for c in clusters.values() if len(c) > 1]


# Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — claims leave by event-clock decay to suspect/dormant, never a timer delete (not yet built)
def consolidate(store: MemoryStore, *, project: str | None = None, subject: str | None = None,
                dry_run: bool = False, semantic: float | None = None,
                accept_corroboration: int | None = 2, judge=None) -> dict:
    """Phase 1: group live claims by exact (subject, scope, project); keep the strongest, supersede the
    rest. Phase 2 (when `semantic` is a cosine max-distance AND claims are embedded): cluster the
    SURVIVORS' different-subject near-duplicates and dedup those too — the LLM-distiller case, where
    each lesson gets a distinct subject but many mean the same thing. Phase 3 (when
    `accept_corroboration` is set, default 2): a candidate survivor backed by >= that many independent
    sessions is PROMOTED to accepted — the swarm-truth gate, so a corroborated lesson surfaces in
    briefing/default recall without a manual --accept. `judge` (s1.agreement_judge) may hold a
    promotion back when the supporting claims look conflicted; it never causes one.
    dry_run returns the plan without writing.
    Returns {groups, superseded, promoted, dry_run, plan:[{subject, survivor, superseded:[ids]}]}."""
    claims = store.live_claims(project, subject)
    by_subject: dict[tuple, list[Claim]] = defaultdict(list)
    for c in claims:
        by_subject[(c.subject, c.scope, c.project)].append(c)
    groups = list(by_subject.values())
    plan = _plan_groups(groups)
    superseded = {cid for p in plan for cid in p["superseded"]}

    if semantic is not None:
        alive = {c.id: c for c in claims if c.id not in superseded}
        sem_plan = _plan_groups(_semantic_clusters(store.similar_pairs(project, max_distance=semantic), alive),
                               keep_updates=False)
        plan += sem_plan
        superseded |= {cid for p in sem_plan for cid in p["superseded"]}

    # Why: accept only when >= K distinct claims from distinct sessions agree; one session's guess must not become the swarm's truth.
    # Promote the survivor of each exact-subject group once enough independent sessions corroborate it.
    # Exact-subject only — semantic (different-subject) merges are too fuzzy to auto-accept on.
    # Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — the model can only hold a promotion back; recurrence is what accepts.
    def held(support: list[Claim]) -> bool:
        p = judge(support) if judge else None
        return p is not None and p < HOLD_BELOW

    promoted = []
    if accept_corroboration:
        for members in groups:
            survivor = _survivor(members)
            # Two conditions, both required: >= K distinct CLAIMS on the subject (so one agent can't
            # self-corroborate with a single multi-source claim) AND >= K distinct source observations
            # (so K claims citing the same one source don't count as K). `not in superseded` is
            # load-bearing under semantic: that pass can supersede an exact-subject survivor (a
            # different-subject near-dup outranked it) — don't promote a just-superseded claim.
            if survivor.status == "candidate" and survivor.id not in superseded \
                    and len(members) >= accept_corroboration \
                    and _corroboration(members) >= accept_corroboration and not held(members):
                promoted.append(survivor.id)
            # Pending updates that corroborate replace the accepted answer.
            updates = [u for u in _updates(members, survivor) if u.id not in superseded]
            if len(updates) >= accept_corroboration and _corroboration(updates) >= accept_corroboration \
                    and not held(updates):
                winner = _survivor(updates)
                promoted.append(winner.id)
                retired = [m.id for m in members if m.id != winner.id and m.id not in superseded]
                superseded |= set(retired)
                plan.append({"subject": winner.subject, "survivor": winner.id, "superseded": retired})

    if not dry_run:
        for cid in superseded:
            store.set_status(cid, "superseded")
        for cid in promoted:
            store.set_status(cid, "accepted")

    return {"groups": len(plan), "superseded": len(superseded), "promoted": len(promoted),
            "dry_run": dry_run, "plan": plan}


def resolve_conflicts(store: MemoryStore, subject: str, *, project: str | None = None) -> dict:
    """Read-only: a subject's live claims grouped by status, with the recommended survivor. Apply via
    consolidate."""
    members = store.live_claims(project, subject)
    by_status: dict[str, list] = defaultdict(list)
    for m in members:
        by_status[m.status].append({"id": m.id, "content": m.content,
                                     "confidence": m.confidence, "updated_at": m.updated_at})
    return {"subject": subject, "count": len(members), "by_status": dict(by_status),
            "recommend": max(members, key=_rank).id if members else None}


def main():
    import argparse

    ap = argparse.ArgumentParser(description="consolidate near-duplicate claims in the memory store")
    ap.add_argument("--project", default=project_from_env())  # same derived per-repo key serve/recall use
    ap.add_argument("--subject", help="limit to one subject (default: whole project)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    ap.add_argument("--semantic", type=float, default=None, metavar="DIST",
                    help="also merge different-subject near-dups within this cosine distance — "
                         "calibrate per embedder (~0.25 for nomic-embed-text); needs KYPP_EMBED_MODEL")
    ap.add_argument("--accept-corroboration", type=int, default=2, metavar="K",
                    help="promote a candidate survivor backed by >= K independent sessions to accepted "
                         "(0 = off, leave acceptance manual)")
    args = ap.parse_args()

    result = consolidate(store_from_env(), project=args.project, subject=args.subject,
                         dry_run=args.dry_run, semantic=args.semantic,
                         accept_corroboration=args.accept_corroboration or None, judge=agreement_judge())
    verb = "would supersede" if args.dry_run else "superseded"
    promo = "would promote" if args.dry_run else "promoted"
    print(f"{result['groups']} duplicate group(s); {verb} {result['superseded']} claim(s); "
          f"{promo} {result['promoted']} corroborated candidate(s) in project {args.project!r}")
    for p in result["plan"][:20]:
        print(f"  keep {p['survivor'][:8]} · drop {len(p['superseded'])} — {p['subject']}")


if __name__ == "__main__" and len(sys.argv) > 1:
    main()
elif __name__ == "__main__":
    # self-test: seed a duplicated subject + a conflicting decision, consolidate, assert dedup.
    import glob

    db = "/tmp/arbiter-selftest.db"
    for f in glob.glob(db + "*"):
        try: os.remove(f)
        except OSError: pass
    store = MemoryStore(db)

    # three claims, same subject — varying strength (accepted > high-conf > low-conf+more-sources)
    store.claim("pitfall", "build fails", "weak", scope="project", project="p", confidence=0.3)
    store.claim("pitfall", "build fails", "mid, more evidence", scope="project", project="p",
                confidence=0.5, source_ids=["o1", "o2"])
    strong = store.claim("pitfall", "build fails", "accepted truth", scope="project", project="p",
                         confidence=0.4, accept=True)
    store.claim("fact", "unrelated", "kept", scope="project", project="p", accept=True)

    # dry-run writes nothing
    dry = consolidate(store, project="p", dry_run=True)
    assert dry["groups"] == 1 and dry["superseded"] == 2, dry
    assert len(store.recall("build fails", project="p", include_candidates=True)) == 3, "dry-run mutated"

    rc = resolve_conflicts(store, "build fails", project="p")
    assert rc["count"] == 3 and rc["recommend"] == strong, rc  # accepted wins the recommendation

    # apply: the accepted claim survives, the other two are superseded (gone from recall)
    res = consolidate(store, project="p")
    assert res["superseded"] == 2 and res["plan"][0]["survivor"] == strong, res
    live = store.recall("build fails", project="p", include_candidates=True)
    assert [c.id for c in live] == [strong], [c.id for c in live]
    assert any(c.subject == "unrelated" for c in store.recall("unrelated", project="p")), "singleton untouched"
    # idempotent: a second pass finds nothing to do
    assert consolidate(store, project="p")["superseded"] == 0, "should be idempotent"

    # an update to an ACCEPTED subject survives consolidation as a pending candidate, and replaces
    # the old answer once a second session backs it
    upd1 = store.claim("pitfall", "build fails", "fixed upstream", scope="project", project="p",
                       confidence=0.4, source_ids=["sU1"])
    consolidate(store, project="p")
    assert store.get(upd1).status == "candidate", "an update must not be superseded by the answer it corrects"
    store.claim("pitfall", "build fails", "fixed upstream, confirmed", scope="project", project="p",
                confidence=0.4, source_ids=["sU2"])
    consolidate(store, project="p")
    now_live = store.recall("build fails", project="p")
    assert len(now_live) == 1 and now_live[0].content.startswith("fixed upstream"), [c.content for c in now_live]
    assert store.get(strong).status == "superseded"
    strong = now_live[0].id

    print(f"OK — arbiter: consolidated 3 dupes → 1 survivor ({strong[:8]}, the accepted claim); "
          f"2 superseded (history kept, recall excludes); singleton untouched; idempotent")

    # acceptance-by-corroboration: two candidate claims, same subject, from DISTINCT sessions →
    # survivor promoted to accepted (surfaces in default recall); a lone candidate stays a candidate.
    store.claim("pitfall", "flaky reparent", "saw it once", scope="project", project="q",
                confidence=0.6, source_ids=["sessA"])
    store.claim("pitfall", "flaky reparent", "saw it again", scope="project", project="q",
                confidence=0.6, source_ids=["sessB"])
    assert consolidate(store, project="q", dry_run=True)["promoted"] == 1, "dry-run should plan the promotion"
    assert not store.recall("flaky reparent", project="q"), "dry-run must not promote (accepted-only recall)"
    res_p = consolidate(store, project="q")
    assert res_p["promoted"] == 1, res_p
    surv = store.recall("flaky reparent", project="q")  # now visible to accepted-only recall
    assert len(surv) == 1 and surv[0].status == "accepted", [(c.status, c.id[:8]) for c in surv]
    store.claim("pitfall", "lonely", "one session only", scope="project", project="q",
                confidence=0.6, source_ids=["sessC"])
    assert consolidate(store, project="q")["promoted"] == 0, "a single session must NOT auto-accept"
    assert not store.recall("lonely", project="q"), "lone candidate stays hidden from accepted recall"
    # a SINGLE claim citing many sources must NOT self-corroborate (needs >= K distinct CLAIMS too)
    store.claim("pitfall", "self-cited", "one claim, many sources", scope="project", project="q",
                confidence=0.6, source_ids=["x1", "x2", "x3"])
    assert consolidate(store, project="q")["promoted"] == 0, "one multi-source claim must NOT self-accept"
    assert not store.recall("self-cited", project="q"), "self-cited lone claim stays candidate"
    print("OK — arbiter corroboration: 2 sessions agreeing → candidate promoted to accepted; "
          "lone candidate stays a candidate")

    # the agreement judge can hold a conflicted promotion back (and a judge with no opinion can't)
    store.claim("fact", "dev port", "the dev server uses port 1", scope="project", project="q",
                confidence=0.6, source_ids=["sessD"])
    store.claim("fact", "dev port", "the dev server uses port 2", scope="project", project="q",
                confidence=0.6, source_ids=["sessE"])
    assert consolidate(store, project="q", subject="dev port", dry_run=True, judge=lambda cs: 0.1)["promoted"] == 0
    assert consolidate(store, project="q", subject="dev port", dry_run=True, judge=lambda cs: None)["promoted"] == 1
    consolidate(store, project="q", subject="dev port", judge=lambda cs: 0.1)
    assert not store.recall("dev port", project="q"), "a conflicted subject must stay a candidate"
    print("OK — arbiter judge: a conflicted pair is held; no opinion never blocks")

    # authority: a HUMAN correction outranks agent claims AND agent corroboration on the same subject.
    # Two agent sessions agree on the WRONG tag; a human asserts the right one → human wins, rest gone.
    store.claim("fact", "image tag", "use pillbox-runner:branch", scope="project", project="r",
                confidence=0.9, source_ids=["a1"])
    store.claim("fact", "image tag", "use pillbox-runner:branch", scope="project", project="r",
                confidence=0.9, source_ids=["a2"])
    human = store.claim("fact", "image tag", "use pillbox-runner:l7", scope="project", project="r",
                        confidence=0.4, authority="human")  # low conf, but human authority dominates
    res_h = consolidate(store, project="r", subject="image tag")
    assert res_h["superseded"] == 2 and res_h["promoted"] == 0, res_h  # both agent claims lose; no promote
    live = store.recall("image tag", project="r")
    assert [c.id for c in live] == [human] and live[0].content.endswith("l7"), \
        [(c.id[:8], c.authority, c.content) for c in live]
    print("OK — arbiter authority: human correction supersedes 2 corroborating agent claims "
          "(low confidence, but authority dominates)")

    # semantic dedup: DIFFERENT subjects, ~identical embeddings → merged (the LLM-distiller case).
    db2 = "/tmp/arbiter-sem-selftest.db"
    for f in glob.glob(db2 + "*"):
        try: os.remove(f)
        except OSError: pass

    def toy(text):  # bag-of-keywords: the two "python interpreter" claims land on the same vector
        t = text.lower()
        return [float("python" in t), float("interpret" in t or "execut" in t), float("test" in t), 1.0]

    s2 = MemoryStore(db2, embed=toy)
    s2.claim("procedure", "Python interpreter execution", "run the python interpreter to execute",
             scope="project", project="p", accept=True)
    s2.claim("procedure", "Python interpreter availability", "check the python interpreter is present",
             scope="project", project="p", confidence=0.5)
    s2.claim("pitfall", "Domino chain validation", "validate the domino chain endpoints match",
             scope="project", project="p", accept=True)
    assert consolidate(s2, project="p", dry_run=True)["superseded"] == 0, "exact pass: subjects all distinct"
    res2 = consolidate(s2, project="p", semantic=0.05)
    live2 = {c.subject for c in s2.live_claims("p")}
    assert res2["superseded"] == 1 and "Domino chain validation" in live2, (res2, live2)
    assert sum("Python" in s for s in live2) == 1, live2  # the two near-dup subjects → one survivor
    print(f"OK — arbiter semantic: 2 different-subject near-dups merged via embeddings → "
          f"{sorted(live2)}; the distinct claim untouched")
