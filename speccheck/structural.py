"""Structural reference checks. These do not use a model."""

from __future__ import annotations

import hashlib

from .model import Advisory, Amendment, Verdict
from .specindex import SpecIndex, doc_ids, load_spec
from .textscan import feature_globs, glob_matches, iter_doc_refs, signoff_lines

CODE_PREFIXES = ("kypp/", "worker/src/", "worker/migrations/", "docs/", ".github/")
PINNED_SPEC_PATHS = (
    ".brief/docs/verify-operator-only/versions/v0002.md",
    ".brief/docs/authority-order/versions/v0004.md",
    ".brief/docs/additive-migrations/versions/v0002.md",
)


def spec_paths(files: dict[str, str]) -> list[str]:
    out = []
    for path in files:
        if path.startswith(".brief/docs/") or path.startswith(".brief/amendments/") or path == ".brief/aliases.yaml":
            out.append(path)
    return out


def amendments_between(baseline: dict[str, str], head: dict[str, str]) -> list[Amendment]:
    keys = set(spec_paths(baseline)) | set(spec_paths(head))
    found = []
    for path in sorted(keys):
        old, new = baseline.get(path), head.get(path)
        if old == new:
            continue
        change = "added" if old is None else "deleted" if new is None else "modified"
        if path == ".brief/docs/features.md":
            detail = (
                "Feature-map edit. Decision rules still come from the baseline; "
                "the head map is checked only as an inventory of its own globs."
            )
        else:
            detail = "Proposed spec change. Decision rules were read from the baseline; this copy was not used as rule text."
            if path in PINNED_SPEC_PATHS:
                detail += " This path is a pinned rule source."
        found.append(Amendment(path=path, change=change, detail=detail))
    return found


def _scanned_pairs(baseline: dict[str, str], head: dict[str, str]) -> list[tuple[str, str, str]]:
    """(tree, path, text) for files whose doc:// refs are in scope."""
    rows = []
    for path, text in sorted(baseline.items()):
        if path.startswith(".brief/"):
            rows.append(("baseline", path, text))
    for path, text in sorted(head.items()):
        if path.startswith(CODE_PREFIXES):
            rows.append(("head", path, text))
    return rows


def dependency_hash(baseline: dict[str, str], head: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for tree, path, text in _scanned_pairs(baseline, head):
        digest.update(tree.encode())
        digest.update(b"\0")
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(text.encode()).digest())
    return digest.hexdigest()


def resolve_ref(index: SpecIndex, project: str, doc: str, rev: str, anchor: str) -> dict:
    ids = doc_ids(index)
    anchors = index.all_anchors()
    if project != "kypp":
        return {"status": "foreign", "detail": f"project {project} is outside this repo"}
    if doc not in ids and doc in anchors and anchor in ids:
        return {
            "status": "swapped",
            "detail": f"{doc} is an anchor id and {anchor} is a document id",
        }
    has_versions = any(i == doc for i, _ in index.versions)
    if rev in {"latest", "current"}:
        if doc in index.aliases:
            number = index.aliases[doc]
            target = index.version(doc, number)
            stale = False
            if target is None:
                return {"status": "broken", "detail": f"alias latest={number} has no version file"}
        elif doc in index.live and not has_versions:
            target = index.live[doc]
            number = None
            stale = False
        else:
            return {"status": "broken", "detail": f"no latest alias for {doc}"}
    else:
        number = int(rev)
        target = index.version(doc, number)
        if target is None:
            return {"status": "broken", "detail": f"version {number} of {doc} does not exist"}
        latest = index.aliases.get(doc)
        stale = latest is not None and number < latest
    if anchor not in target.anchors:
        return {"status": "broken", "detail": f"anchor {anchor} is not in {target.path}"}
    return {"status": "resolved", "stale": stale, "version": number, "target": target.path, "detail": target.path}


def build_reference_index(baseline: dict[str, str], head: dict[str, str]) -> dict:
    index = load_spec(baseline)
    refs = []
    for tree, path, text in _scanned_pairs(baseline, head):
        for raw, project, doc, rev, anchor, line in iter_doc_refs(text):
            resolved = resolve_ref(index, project, doc, rev, anchor)
            refs.append({"tree": tree, "path": path, "line": line, "ref": raw, "doc": doc, "anchor": anchor, "rev": rev, **resolved})
    return {"refs": refs}


def doc_ref_verdicts(payload: dict) -> tuple[Verdict, list[Advisory]]:
    refs = payload.get("refs") or []
    kypp = [r for r in refs if r.get("status") != "foreign"]
    foreign = [r for r in refs if r.get("status") == "foreign"]
    bad = [r for r in kypp if r.get("status") in {"broken", "swapped"}]
    scope = "every doc://kypp ref in baseline .brief and in head code under kypp/, worker/, docs/, and .github/"
    coverage = f"examined {len(kypp)} ref(s)"
    advisories = [
        Advisory(code="foreign-ref", detail=f"{r['path']}: {r['ref']} ({r['detail']})", path=r["path"])
        for r in foreign
    ]
    stale_groups: dict[str, list[dict]] = {}
    for ref in kypp:
        if not ref.get("stale"):
            continue
        version = ref.get("version")
        pin = f"doc://kypp/{ref.get('doc')}@{int(version):04d}#{ref.get('anchor')}" if isinstance(version, int) else ref["ref"]
        stale_groups.setdefault(pin, []).append(ref)
    for pin in sorted(stale_groups):
        rows = stale_groups[pin]
        occurrences = []
        seen: set[tuple[str, int]] = set()
        for ref in rows:
            key = (ref["path"], int(ref.get("line") or 0))
            if key in seen:
                continue
            seen.add(key)
            occurrences.append(f"{ref['path']}:{ref.get('line')}")
        occurrences.sort()
        advisories.append(
            Advisory(
                code="stale-pin",
                detail=(
                    f"{pin} resolves, and a newer version is latest; version lag is advisory. "
                    f"Occurrences: {', '.join(occurrences)}"
                ),
                path=occurrences[0].split(":", 1)[0] if len({item.split(":", 1)[0] for item in occurrences}) == 1 else None,
            )
        )
    if bad:
        example = "; ".join(f"{r['path']}: {r['ref']} ({r['detail']})" for r in bad[:8])
        return (
            Verdict(
                rule_id="doc-refs",
                status="violates",
                severity="low",
                claim_text="doc:// refs resolve to a document id, a version that exists, and an anchor in that version",
                scope=scope,
                coverage=coverage,
                detail=f"{len(bad)} ref(s) do not resolve",
                counterexample=example,
                evidence="structural",
            ),
            advisories,
        )
    return (
        Verdict(
            rule_id="doc-refs",
            status="conforms",
            claim_text="doc:// refs resolve to a document id, a version that exists, and an anchor in that version",
            scope=scope,
            coverage=coverage,
            detail="every examined ref resolved",
            evidence="structural",
        ),
        advisories,
    )


def alias_verdict(index: SpecIndex, files: dict[str, str]) -> Verdict:
    scope = ".brief/aliases.yaml latest entries against .brief/docs/<id>/versions/vNNNN.md"
    problems = []
    versioned: dict[str, set[int]] = {}
    for (doc_id, number) in index.versions:
        versioned.setdefault(doc_id, set()).add(number)
    if ".brief/aliases.yaml" not in files and versioned:
        return Verdict(
            rule_id="aliases",
            status="unresolved",
            claim_text="aliases.yaml latest matches the version files",
            scope=scope,
            coverage="aliases.yaml absent",
            detail="versioned docs exist and aliases.yaml is missing, so latest cannot be checked",
            evidence="structural",
        )
    for doc_id, numbers in sorted(versioned.items()):
        latest = index.aliases.get(doc_id)
        if latest is None:
            problems.append(f"{doc_id} has versions but no aliases.yaml latest")
            continue
        if latest not in numbers:
            problems.append(f"{doc_id} latest={latest} has no v{latest:04d}.md")
        if latest != max(numbers):
            problems.append(f"{doc_id} latest={latest} but the highest version file is v{max(numbers):04d}.md")
    for doc_id, latest in sorted(index.aliases.items()):
        if doc_id not in versioned:
            problems.append(f"{doc_id} latest={latest} but there are no version files")
    for doc in list(index.live.values()) + list(index.versions.values()):
        path_id = doc.path.split("/")[2] if "/versions/" in doc.path else doc.path.rsplit("/", 1)[-1][:-3]
        if doc.id != path_id:
            problems.append(f"{doc.path} front matter id {doc.id} does not match {path_id}")
    coverage = f"checked {len(versioned)} versioned doc(s) and {len(index.aliases)} alias(es)"
    claim = "aliases.yaml latest matches the highest version file for each doc"
    if problems:
        return Verdict(
            rule_id="aliases",
            status="violates",
            severity="low",
            claim_text=claim,
            scope=scope,
            coverage=coverage,
            detail=f"{len(problems)} alias disagreement(s)",
            counterexample="; ".join(problems[:8]),
            evidence="structural",
        )
    return Verdict(
        rule_id="aliases",
        status="conforms",
        claim_text=claim,
        scope=scope,
        coverage=coverage,
        detail="latest matches the version files",
        evidence="structural",
    )


def signoff_verdict(head: dict[str, str], index: SpecIndex) -> Verdict | None:
    text = head.get(".brief/SIGNOFF")
    if text is None:
        return None
    scope = "every line of head .brief/SIGNOFF names an anchor that exists in the baseline spec"
    lines = signoff_lines(text)
    bad = []
    anchors = index.all_anchors()
    for lineno, anchor, raw in lines:
        if anchor is None:
            bad.append(f"line {lineno} is not a sign-off: {raw.strip()[:80]}")
        elif anchor not in anchors:
            bad.append(f"line {lineno} anchor {anchor} exists in no baseline doc")
    coverage = f"examined {len(lines)} sign-off line(s); a conforms line is not evidence"
    claim = "SIGNOFF anchors exist in a baseline document"
    if bad:
        return Verdict(
            rule_id="signoff-anchors",
            status="violates",
            severity="low",
            claim_text=claim,
            scope=scope,
            coverage=coverage,
            detail=f"{len(bad)} sign-off line(s) do not resolve",
            counterexample="; ".join(bad[:8]),
            evidence="structural",
        )
    return Verdict(
        rule_id="signoff-anchors",
        status="conforms",
        claim_text=claim,
        scope=scope,
        coverage=coverage,
        detail="every sign-off anchor exists in the baseline spec; the reason text was not treated as evidence",
        evidence="structural",
    )


def feature_path_verdict(head: dict[str, str], head_paths: list[str]) -> Verdict | None:
    text = head.get(".brief/docs/features.md")
    if text is None:
        return None
    globs = feature_globs(text)
    missing = [g for g in globs if not glob_matches(g, head_paths)]
    scope = "every paths: glob in head .brief/docs/features.md"
    coverage = f"matched {len(globs) - len(missing)} of {len(globs)} glob(s) against {len(head_paths)} path(s)"
    claim = "every feature-map glob matches at least one file"
    if missing:
        return Verdict(
            rule_id="feature-paths",
            status="violates",
            severity="low",
            claim_text=claim,
            scope=scope,
            coverage=coverage,
            detail=f"{len(missing)} glob(s) match nothing",
            counterexample=", ".join(missing[:8]),
            evidence="structural",
        )
    return Verdict(
        rule_id="feature-paths",
        status="conforms",
        claim_text=claim,
        scope=scope,
        coverage=coverage,
        detail="every glob matches a file",
        evidence="structural",
    )


def overlap_advisories(index: SpecIndex, head_paths: list[str]) -> list[Advisory]:
    docs = []
    seen = set()
    for doc_id in list(index.live) + [i for i, _ in index.versions]:
        if doc_id in seen or not index.is_active(doc_id):
            continue
        seen.add(doc_id)
        doc = index.latest_doc(doc_id)
        if doc and doc.related_code:
            docs.append(doc)
    advisories = []
    for i, left in enumerate(docs):
        left_hits = {p for g in left.related_code for p in head_paths if glob_matches(g, [p])}
        for right in docs[i + 1 :]:
            right_hits = {p for g in right.related_code for p in head_paths if glob_matches(g, [p])}
            shared = sorted(left_hits & right_hits)
            if not shared:
                continue
            if left.id in right.body or right.id in left.body:
                continue
            advisories.append(
                Advisory(
                    code="related-code-overlap",
                    detail=(
                        f"{left.id} and {right.id} both match {shared[0]}"
                        + (f" (+{len(shared) - 1})" if len(shared) > 1 else "")
                        + "; neither document mentions the other. Overlap is not a violation;"
                        + " contradiction between prose invariants is deferred."
                    ),
                    path=shared[0],
                )
            )
    return advisories
