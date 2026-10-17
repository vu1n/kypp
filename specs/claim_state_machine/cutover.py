"""Synthetic cutover inventory, NOT a database migration or runtime command.

This independent, read-only specification deliberately imports neither kernel nor
legacy rank/group helpers. It inventories raw rows (including retired history),
not hydrated live claims. Unsupported/unplaced rows block activation; the result
cannot be passed to ClaimCommands. See docs/claim-cutover.md for remaining choices.
"""
from copy import deepcopy
import json


def canonical_group(row):
    subject, scope = row["subject"], row["scope"]
    if not subject.strip():
        raise ValueError("empty subject")
    if scope == "project" and row.get("project") is None:
        return None  # Preserve unsorted, never invent a project or shared null group.
    if scope in ("project", "group") and row.get("project"):
        return (subject, scope, row["project"], None)
    if scope == "global":
        return (subject, scope, None, None)
    if scope in ("user", "room") and row.get("user"):
        return (subject, scope, None, row["user"])
    raise ValueError("unsupported placement or missing owner")


def inventory(claims, observations=(), pointers=()):
    """Freeze an archive and describe canonical groups/duplicate-head choices.

    Only already accepted rows compete. Legacy authority, confidence, source-count
    and recency break ties, then greatest full ID in UTF-8 byte order. Source count
    is ONLY a legacy migration tie-break, not a recurrence/admission grant.
    All check results, acceptance causes and source freshness remain unknown.
    """
    archive = deepcopy({"claims": list(claims), "observations": list(observations),
                        "pointers": list(pointers)})
    grouped, unplaced, unsupported = {}, [], []
    seen = set()
    for row in archive["claims"]:
        if row["id"] in seen:
            raise ValueError("duplicate claim id")
        seen.add(row["id"])
        if row["status"] not in ("candidate", "accepted", "rejected", "superseded"):
            raise ValueError("unsupported lifecycle")
        if row["authority"] not in ("agent", "verified", "human"):
            raise ValueError("unsupported authority")
        try:
            key = canonical_group(row)
        except ValueError as error:
            unsupported.append({"id": row["id"], "reason": str(error)})
            continue
        if key is None:
            unplaced.append(row["id"])
        else:
            grouped.setdefault(key, []).append(row)

    groups = []
    for key, rows in grouped.items():
        accepted = [row for row in rows if row["status"] == "accepted"]
        def rank(row):
            sources = row["source_ids"]
            if isinstance(sources, str):
                sources = json.loads(sources)
            return (("agent", "verified", "human").index(row["authority"]),
                    row.get("confidence") or 0, len(sources),
                    row["updated_at"], row["id"].encode("utf-8"))
        head = max(accepted, key=rank)["id"] if accepted else None
        groups.append({
            "key": list(key), "head": head,
            "members": sorted(row["id"] for row in rows),
            "supersede": sorted(row["id"] for row in accepted if row["id"] != head),
            "acceptance": "legacy_unknown" if head else None,
            "check": None,
        })

    # Correlation only, within this one source archive. A bare legacy session string
    # is not a globally namespaced/trusted source and never becomes a fresh Stamp.
    aliases = {}
    for observation in archive["observations"]:
        if observation.get("source"):
            aliases.setdefault(observation["source"], []).append(observation["id"])
    return {"archive": archive, "groups": groups, "unplaced": sorted(unplaced),
            "unsupported": unsupported,
            "recorded_source_aliases": {key: sorted(ids) for key, ids in aliases.items()},
            "recurrence_sources": []}
