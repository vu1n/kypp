"""False-conformance rate, abstention, and coverage.

Smoke controls are scored apart and labeled smoke only. They do not enter the
false-conformance denominator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bench_cases import Case, all_cases
from .engine import run_check
from .model import Report


@dataclass
class SplitScore:
    split: str
    cases: int
    judged: int
    false_conformance_rate: float | None
    abstention_rate: float | None
    coverage: float | None
    false_flag_rate: float | None
    smoke_only: bool
    ok: bool
    failures: list[str] = field(default_factory=list)


def _status(report: Report, rule_id: str) -> str | None:
    for verdict in report.verdicts:
        if verdict.rule_id == rule_id:
            return verdict.status
    return None


def _run(case: Case) -> Report:
    return run_check(
        case.baseline,
        case.head,
        baseline_sha=f"base-{case.id}",
        head_sha=f"head-{case.id}",
        baseline_paths=sorted(case.baseline),
        head_paths=sorted(case.head),
    )


def _check_case(case: Case, report: Report, failures: list[str]) -> None:
    got_violations = {v.rule_id for v in report.by_status("violates")}
    if case.expect_violation_ids is not None and got_violations != case.expect_violation_ids:
        failures.append(
            f"{case.id}: violations {sorted(got_violations)} != {sorted(case.expect_violation_ids)}"
        )
    for rule_id, expected in case.expect_status.items():
        got = _status(report, rule_id)
        if got != expected:
            failures.append(f"{case.id}: {rule_id} expected {expected}, got {got}")
    for rule_id, severity in case.expect_severity.items():
        verdict = next((v for v in report.verdicts if v.rule_id == rule_id), None)
        if verdict is None or verdict.severity != severity:
            failures.append(f"{case.id}: {rule_id} severity expected {severity}, got {None if verdict is None else verdict.severity}")
    if case.expect_amendments is not None and bool(report.amendments) != case.expect_amendments:
        failures.append(f"{case.id}: amendments present={bool(report.amendments)} expected {case.expect_amendments}")
    if case.expect_advisory and not any(a.code == case.expect_advisory for a in report.advisories):
        failures.append(f"{case.id}: missing advisory {case.expect_advisory}")
    if case.expect_advisory_count is not None:
        matched = [a for a in report.advisories if a.code == case.expect_advisory]
        if len(matched) != case.expect_advisory_count:
            failures.append(
                f"{case.id}: {case.expect_advisory} count {len(matched)} != {case.expect_advisory_count}"
            )
        detail = "\n".join(a.detail for a in matched)
        for needle in case.expect_advisory_contains:
            if needle not in detail:
                failures.append(f"{case.id}: advisory detail missing {needle}")
    if case.expect_not_clean_single_writer:
        writer = next((v for v in report.verdicts if v.rule_id == "human-authority-writers"), None)
        if writer is None or writer.clean_single_writer is not False or "POST /sync" not in writer.claim_text:
            failures.append(f"{case.id}: writer claim was reported as a clean single-writer or dropped POST /sync")
        if any(v.clean_single_writer is True and "shell.correct_main" in v.claim_text for v in report.verdicts):
            failures.append(f"{case.id}: a clean single-writer claim still names shell.correct_main")


def score_split(split: str, cases: list[Case]) -> SplitScore:
    smoke_only = split == "smoke"
    failures: list[str] = []
    pairs: list[tuple[str, str | None]] = []
    flagged = 0
    for case in cases:
        report = _run(case)
        _check_case(case, report, failures)
        if smoke_only and report.by_status("violates"):
            flagged += 1
        if smoke_only:
            continue
        for rule_id, expected in case.expect_status.items():
            got = _status(report, rule_id)
            pairs.append((expected, got))
    judged = len(pairs)
    nonconform = [got for expected, got in pairs if expected != "conforms"]
    false_hits = [got for expected, got in pairs if expected != "conforms" and got == "conforms"]
    abstentions = [got for _, got in pairs if got in {None, "unresolved", "unsupported"}]
    covered = [got for _, got in pairs if got in {"conforms", "violates"}]
    return SplitScore(
        split=split,
        cases=len(cases),
        judged=judged,
        false_conformance_rate=(len(false_hits) / len(nonconform)) if nonconform else None,
        abstention_rate=(len(abstentions) / judged) if judged else None,
        coverage=(len(covered) / judged) if judged else None,
        false_flag_rate=(flagged / len(cases)) if smoke_only and cases else None,
        smoke_only=smoke_only,
        ok=not failures and (flagged == 0 if smoke_only else True),
        failures=failures,
    )


def run_benchmark(splits: list[str] | None = None) -> list[SplitScore]:
    wanted = splits or ["tuning", "regression", "holdout", "smoke"]
    grouped: dict[str, list[Case]] = {name: [] for name in wanted}
    for case in all_cases():
        if case.split in grouped:
            grouped[case.split].append(case)
    return [score_split(name, grouped[name]) for name in wanted]


def format_scores(scores: list[SplitScore]) -> str:
    lines = ["# Spec-check benchmark", ""]
    for score in scores:
        label = " (smoke only — not an accuracy metric)" if score.smoke_only else ""
        lines.append(f"## {score.split}{label}")
        lines.append("")
        lines.append(f"- cases: {score.cases}")
        if score.smoke_only:
            rate = "n/a" if score.false_flag_rate is None else f"{score.false_flag_rate:.3f}"
            lines.append(f"- false-flag rate: {rate}")
        else:
            fc = "n/a" if score.false_conformance_rate is None else f"{score.false_conformance_rate:.3f}"
            ab = "n/a" if score.abstention_rate is None else f"{score.abstention_rate:.3f}"
            cov = "n/a" if score.coverage is None else f"{score.coverage:.3f}"
            lines.append(f"- judged claim rows: {score.judged}")
            lines.append(f"- false-conformance rate: {fc}")
            lines.append(f"- abstention rate: {ab}")
            lines.append(f"- coverage: {cov}")
        if score.failures:
            lines.append("- failures:")
            for failure in score.failures:
                lines.append(f"  - {failure}")
        lines.append("")
    return "\n".join(lines)
