"""Verdicts and the advisory report.

Outcomes stay separate. A bounded proposition is not a semantic pass, and
"no counterexample found" is not assurance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


# violation: a counterexample is established in scope.
# candidate: a suspicious construction whose target or execution is unproved.
# no_counterexample: the scan found none. This is not assurance.
# unresolved / unsupported: the check does not answer.
# bounded: a closed-world proposition over an enumerated set, worded as that proposition.
STATUSES = (
    "violates",
    "candidate",
    "no_counterexample",
    "unresolved",
    "unsupported",
    "bounded",
)
SEVERITIES = ("info", "low", "medium", "high")
# Report sections that are not rule verdicts. guard_verified is unused.
# test_passed is recorded by the contract tests, never inferred from a skip.
REPORT_OUTCOMES = ("test_passed", "guard_verified")


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
        if self.status == "bounded":
            if not self.scope or not self.coverage:
                raise ValueError("bounded requires a defined scope and coverage evidence")
            if self.counterexample:
                raise ValueError("bounded cannot carry a counterexample")
        if self.status == "no_counterexample" and self.counterexample:
            raise ValueError("no_counterexample cannot carry a counterexample")
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
    environment: str = ""
    tests_passed: list[str] = field(default_factory=list)
    guards_verified: list[str] = field(default_factory=list)

    def by_status(self, status: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == status]

    @property
    def counts(self) -> dict[str, int]:
        return {s: len(self.by_status(s)) for s in STATUSES}

    def to_dict(self) -> dict:
        return {
            "baseline_sha": self.baseline_sha,
            "head_sha": self.head_sha,
            "environment": self.environment,
            "advisory": True,
            "blocks_merge": False,
            "counts": self.counts,
            "verdicts": [asdict(v) for v in self.verdicts],
            "advisories": [asdict(a) for a in self.advisories],
            "amendments": [asdict(a) for a in self.amendments],
            "notes": list(self.notes),
            "cache": self.cache,
            "test_passed": list(self.tests_passed),
            "guard_verified": list(self.guards_verified),
        }
