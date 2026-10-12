"""Verdicts and the advisory report."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


STATUSES = ("conforms", "violates", "unresolved", "unsupported")
SEVERITIES = ("info", "low", "medium", "high")


@dataclass
class Verdict:
    rule_id: str
    status: str
    claim_text: str
    scope: str
    coverage: str
    detail: str
    spec: str | None = None
    severity: str | None = None
    counterexample: str | None = None
    evidence: str = "none"
    clean_single_writer: bool | None = None
    code_revision: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status}")
        if self.severity is not None and self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity}")
        if self.status == "conforms":
            if not self.scope or not self.coverage:
                raise ValueError("conforms requires a defined scope and coverage evidence")
            if self.counterexample:
                raise ValueError("conforms cannot carry a counterexample")
        if self.status == "violates" and self.severity is None:
            raise ValueError("violates requires a severity")


@dataclass
class Advisory:
    code: str
    detail: str
    severity: str = "info"
    path: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity}")


@dataclass
class Amendment:
    path: str
    change: str  # added, deleted, modified
    detail: str


@dataclass
class Report:
    baseline_sha: str
    head_sha: str
    verdicts: list[Verdict] = field(default_factory=list)
    advisories: list[Advisory] = field(default_factory=list)
    amendments: list[Amendment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    def by_status(self, status: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == status]

    @property
    def counts(self) -> dict[str, int]:
        return {s: len(self.by_status(s)) for s in STATUSES}

    def to_dict(self) -> dict:
        return {
            "baseline_sha": self.baseline_sha,
            "head_sha": self.head_sha,
            "advisory": True,
            "blocks_merge": False,
            "counts": {
                "checked": self.counts["conforms"],
                "violated": self.counts["violates"],
                "unsupported": self.counts["unsupported"],
                "unresolved": self.counts["unresolved"],
            },
            "verdicts": [asdict(v) for v in self.verdicts],
            "advisories": [asdict(a) for a in self.advisories],
            "amendments": [asdict(a) for a in self.amendments],
            "notes": list(self.notes),
            "cache": self.cache,
        }
