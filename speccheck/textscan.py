"""Structural text: tiny YAML, invariant bullets, adjoining clauses, doc:// refs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


DOC_REF_RE = re.compile(
    r"doc://([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)@(latest|current|\d{1,4})#([A-Za-z0-9_-]+)"
)

_FORWARD = re.compile(
    r"^(the one other\b|except\b|unless\b|however\b|this does not\b)",
    re.I,
)
_WRITER = re.compile(r"\bonly\b.{0,240}\b(writes|sets|caller)\b", re.I | re.S)
_EXCEPTION = re.compile(r"\b(except|unless|other writer)\b|POST /sync", re.I)


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def coerce(value: str):
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_yaml_subset(text: str) -> dict:
    """The subset Brief front matter and aliases.yaml actually use."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), raw.strip()))

    def parse_block(i: int, indent: int):
        if i >= len(lines) or lines[i][0] < indent:
            return {}, i
        if lines[i][0] == indent and lines[i][1].startswith("- "):
            items = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                items.append(unquote(lines[i][1][2:].strip()))
                i += 1
            return items, i
        obj: dict = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            key, _, val = lines[i][1].partition(":")
            key, val = key.strip(), val.strip()
            i += 1
            if val == "":
                child, i = parse_block(i, indent + 2)
                obj[key] = child
            else:
                obj[key] = coerce(unquote(val))
        return obj, i

    tree, _ = parse_block(0, 0)
    return tree if isinstance(tree, dict) else {}


def split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    return parse_yaml_subset(text[4:end]), text[end + 5 :]


def invariant_bullets(body: str) -> list[str]:
    """Bullets under ``### Invariant`` only. Prose outside that heading is not extracted."""
    bullets: list[str] = []
    buf: list[str] = []
    in_inv = False

    def flush() -> None:
        if buf:
            bullets.append(normalize_ws(" ".join(buf)))
            buf.clear()

    for line in body.splitlines():
        if line.startswith("### "):
            if in_inv:
                break
            in_inv = line.strip().lower() == "### invariant"
            continue
        if not in_inv:
            continue
        if line.startswith("#"):
            break
        if line.startswith("- "):
            flush()
            buf.append(line[2:].strip())
        elif buf and (line.startswith("  ") or line.startswith("\t")):
            buf.append(line.strip())
        elif not line.strip():
            flush()
    flush()
    return bullets


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def authority_tokens(text: str) -> set[str]:
    toks = {m.lower() for m in re.findall(r"authority\s*=\s*[`\"']*([A-Za-z]+)", text)}
    toks |= {m.lower() for m in re.findall(r"\bat\s+`([A-Za-z]+)`", text)}
    return toks


def qualifies_forward(prev: str, nxt: str) -> bool:
    if _FORWARD.match(nxt.strip()):
        return True
    if re.search(r"\bother writer\b", nxt, re.I) and re.search(r"\bonly\b", prev, re.I):
        return True
    return False


def adjoining_writer_exception(prev: str, cur: str) -> bool:
    if not _EXCEPTION.search(prev) and "POST /sync" not in prev:
        return False
    shared = authority_tokens(prev) & authority_tokens(cur)
    return bool(shared)


@dataclass
class ClaimGroup:
    bullets: list[str]
    text: str
    anchor: str
    doc_id: str
    version: str
    index: int
    clean_single_writer: bool | None = None
    context: str = ""
    bullet_indexes: list[int] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        if self.context:
            return self.context + "\n" + self.text
        return self.text

    @property
    def group_id(self) -> str:
        return f"{self.doc_id}@{self.version}#{self.anchor}#{self.index}"


def group_bullets(bullets: list[str], *, doc_id: str, version: str, anchor: str) -> list[ClaimGroup]:
    """Keep a bullet with the next one when the next one is an exception or another writer.

    A sole-writer sentence is not reported as a clean single-writer claim when an
    adjoining bullet permits another writer (the authority-order ``POST /sync`` case).
    """
    groups: list[ClaimGroup] = []
    i = 0
    while i < len(bullets):
        parts = [bullets[i]]
        indexes = [i]
        j = i + 1
        while j < len(bullets) and qualifies_forward(parts[-1], bullets[j]):
            parts.append(bullets[j])
            indexes.append(j)
            j += 1
        groups.append(
            ClaimGroup(
                bullets=parts,
                text="\n".join(parts),
                anchor=anchor,
                doc_id=doc_id,
                version=version,
                index=indexes[0],
                bullet_indexes=indexes,
            )
        )
        i = j
    for idx, group in enumerate(groups):
        own = group.text
        if not _WRITER.search(own):
            group.clean_single_writer = None
            continue
        qualified = len(group.bullets) > 1 or bool(_EXCEPTION.search(own))
        if not qualified and idx > 0:
            prev = groups[idx - 1].text
            if adjoining_writer_exception(prev, own):
                qualified = True
                group.context = prev
        group.clean_single_writer = not qualified
    return groups


def anchor_ids(body: str) -> list[str]:
    return re.findall(r"<!--\s*brief:anchor\s+([A-Za-z0-9_-]+)\s*-->", body)


def feature_globs(text: str) -> list[str]:
    globs: list[str] = []
    for block in re.findall(r"```yaml\n(.*?)```", text, re.S):
        if "paths:" not in block:
            continue
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                globs.append(unquote(stripped[2:].strip()))
    return globs


def glob_matches(pattern: str, paths: list[str]) -> bool:
    rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return any(re.fullmatch(rx, path) for path in paths)


def iter_doc_refs(text: str):
    for match in DOC_REF_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        yield match.group(0), match.group(1), match.group(2), match.group(3), match.group(4), line


SIGNOFF_RE = re.compile(r"^([A-Za-z0-9_-]+)\s+(conforms|amend-proposed)\s*:\s*(.*)$")


def signoff_lines(text: str) -> list[tuple[int, str | None, str]]:
    """Return (lineno, anchor or None, raw). ``anchor`` is None when the line is not a sign-off."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        match = SIGNOFF_RE.match(raw.strip())
        if match:
            out.append((n, match.group(1), raw))
        else:
            out.append((n, None, raw))
    return out
