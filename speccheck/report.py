"""Render one advisory report. The checker does not post it and does not block merge."""

from __future__ import annotations

from .model import Report

_TITLES = (
    ("violates", "Violated"),
    ("conforms", "Checked (conforms)"),
    ("unsupported", "Unsupported"),
    ("unresolved", "Unresolved"),
)


def _clip(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render_markdown(report: Report) -> str:
    counts = report.counts
    lines = [
        "# Spec consistency (advisory)",
        "",
        "This report does not block merge. The checker does not write `.brief/` or SIGNOFF and does not ratify anything.",
        "",
        f"Baseline: `{report.baseline_sha}`",
        f"Head: `{report.head_sha}`",
        "",
        (
            f"checked (conforms): {counts['conforms']} | violated: {counts['violates']} | "
            f"unsupported: {counts['unsupported']} | unresolved: {counts['unresolved']}"
        ),
        "",
    ]
    for note in report.notes:
        lines.append(f"- {note}")
    if report.notes:
        lines.append("")
    for status, title in _TITLES:
        rows = report.by_status(status)
        lines.append(f"## {title} ({len(rows)})")
        lines.append("")
        if not rows:
            lines.append("None.")
            lines.append("")
            continue
        for row in rows:
            severity = f" [{row.severity}]" if row.severity else ""
            lines.append(f"- `{row.rule_id}`{severity} — {_clip(row.detail, 300)}")
            if row.spec:
                lines.append(f"  - spec: `{row.spec}`")
            if row.claim_text:
                lines.append(f"  - claim: {_clip(row.claim_text)}")
            if row.clean_single_writer is False:
                lines.append("  - not a clean single-writer claim")
            lines.append(f"  - scope: {_clip(row.scope, 300)}")
            lines.append(f"  - coverage: {_clip(row.coverage, 300)}")
            if row.counterexample:
                lines.append(f"  - counterexample: {_clip(row.counterexample, 400)}")
        lines.append("")
    lines.append(f"## Proposed spec amendments ({len(report.amendments)})")
    lines.append("")
    if not report.amendments:
        lines.append("None. Head spec matches the baseline.")
        lines.append("")
    else:
        lines.append("These are not the rules. Code was checked against the baseline.")
        lines.append("")
        for item in report.amendments:
            lines.append(f"- `{item.change}` `{item.path}` — {item.detail}")
        lines.append("")
    lines.append(f"## Advisories ({len(report.advisories)})")
    lines.append("")
    if not report.advisories:
        lines.append("None.")
        lines.append("")
    else:
        for item in report.advisories:
            where = f" `{item.path}`" if item.path else ""
            lines.append(f"- [{item.severity}] `{item.code}`{where} — {_clip(item.detail, 300)}")
        lines.append("")
    cache = report.cache
    if cache:
        lines.append("## Cache")
        lines.append("")
        lines.append(
            "syntax hits {syntax_hits}, misses {syntax_misses}; "
            "reference builds {ref_builds}, head hits {ref_hits}, "
            "dependency reuses {dependency_reuses}, base-SHA rows rejected {rejected_base}.".format(**cache)
        )
        lines.append("")
    return "\n".join(lines)
