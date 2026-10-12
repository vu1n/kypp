"""Load a Brief tree and resolve doc:// refs against it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textscan import anchor_ids, invariant_bullets, parse_yaml_subset, split_front_matter


VERSION_RE = re.compile(r"^\.brief/docs/([^/]+)/versions/v(\d{4})\.md$")
LIVE_RE = re.compile(r"^\.brief/docs/([^/]+)\.md$")


@dataclass
class SpecDoc:
    id: str
    path: str
    status: str
    type: str
    anchors: list[str]
    related_code: list[str]
    body: str
    version: int | None
    bullets: list[str]


@dataclass
class SpecIndex:
    live: dict[str, SpecDoc] = field(default_factory=dict)
    versions: dict[tuple[str, int], SpecDoc] = field(default_factory=dict)
    aliases: dict[str, int] = field(default_factory=dict)
    anchor_owners: dict[str, set[str]] = field(default_factory=dict)

    def version(self, doc_id: str, number: int) -> SpecDoc | None:
        return self.versions.get((doc_id, number))

    def latest_doc(self, doc_id: str) -> SpecDoc | None:
        if doc_id in self.aliases and (doc_id, self.aliases[doc_id]) in self.versions:
            return self.versions[(doc_id, self.aliases[doc_id])]
        numbers = [n for (i, n) in self.versions if i == doc_id]
        if numbers:
            return self.versions[(doc_id, max(numbers))]
        return self.live.get(doc_id)

    def is_active(self, doc_id: str) -> bool:
        doc = self.latest_doc(doc_id)
        return bool(doc and doc.status == "active" and doc.type != "features")

    def all_anchors(self) -> set[str]:
        return set(self.anchor_owners)


def _doc_from(path: str, text: str, doc_id: str, version: int | None) -> SpecDoc:
    meta, body = split_front_matter(text)
    related = meta.get("related_code") or []
    if not isinstance(related, list):
        related = []
    anchors = anchor_ids(body)
    return SpecDoc(
        id=str(meta.get("id") or doc_id),
        path=path,
        status=str(meta.get("status") or ""),
        type=str(meta.get("type") or ""),
        anchors=anchors,
        related_code=[str(x) for x in related],
        body=body,
        version=version,
        bullets=invariant_bullets(body),
    )


def load_spec(files: dict[str, str]) -> SpecIndex:
    index = SpecIndex()
    alias_text = files.get(".brief/aliases.yaml")
    if alias_text is not None:
        parsed = parse_yaml_subset(alias_text)
        for doc_id, body in parsed.items():
            if isinstance(body, dict) and "latest" in body:
                try:
                    index.aliases[doc_id] = int(body["latest"])
                except (TypeError, ValueError):
                    continue
    for path, text in files.items():
        version_m = VERSION_RE.match(path)
        live_m = LIVE_RE.match(path)
        if version_m:
            doc_id, num = version_m.group(1), int(version_m.group(2))
            doc = _doc_from(path, text, doc_id, num)
            index.versions[(doc_id, num)] = doc
        elif live_m:
            doc_id = live_m.group(1)
            doc = _doc_from(path, text, doc_id, None)
            index.live[doc_id] = doc
        else:
            continue
        for anchor in doc.anchors:
            index.anchor_owners.setdefault(anchor, set()).add(doc.id)
    return index


def doc_ids(index: SpecIndex) -> set[str]:
    ids = set(index.live)
    ids.update(i for i, _ in index.versions)
    return ids
