"""Detection, unresolved rate, false violations, noisy candidates, and mutation detection.

Smoke controls are scored apart. They do not enter the other rates.
An empty requested split fails. "No counterexample" is not a pass.
Known-violation detection counts only the scored cases that expect a violation.
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
    known_violation_detection: float | None
    unresolved_rate: float | None
    false_positive_rate: float | None
    noisy_candidate_rate: float | None
    mutation_detection: float | None
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


def _rate(hits: int, total: int) -> float | None:
    return (hits / total) if total else None


def score_split(split: str, cases: list[Case]) -> SplitScore:
    smoke_only = split == "smoke"
    failures: list[str] = []
    if not cases:
        return SplitScore(
            split=split,
            cases=0,
            judged=0,
            known_violation_detection=None,
            unresolved_rate=None,
            false_positive_rate=None,
            noisy_candidate_rate=None,
            mutation_detection=None,
            smoke_only=smoke_only,
            ok=False,
            failures=[f"{split}: empty split"],
        )
    pairs: list[tuple[str, str | None]] = []
    mutation_pairs: list[tuple[str, str | None]] = []
    flagged = 0
    noisy_cases = 0
    for case in cases:
        report = _run(case)
        _check_case(case, report, failures)
        if smoke_only and report.by_status("violates"):
            flagged += 1
        if smoke_only and report.by_status("candidate"):
            noisy_cases += 1
        if smoke_only:
            continue
        independent = case.id.startswith("worker_fresh_")
        for rule_id, expected in case.expect_status.items():
            got = _status(report, rule_id)
            pairs.append((expected, got))
            if independent:
                mutation_pairs.append((expected, got))
    judged = len(pairs)
    expected_violations = [(expected, got) for expected, got in pairs if expected == "violates"]
    detected = [got for _, got in expected_violations if got == "violates"]
    unresolved = [got for _, got in pairs if got in {None, "unresolved"}]
    clean = [(expected, got) for expected, got in pairs if expected != "violates"]
    false_positives = [got for _, got in clean if got == "violates"]
    quiet = [(expected, got) for expected, got in pairs if expected not in {"violates", "candidate"}]
    noisy_candidates = [got for _, got in quiet if got == "candidate"]
    mutation_expected = [(expected, got) for expected, got in mutation_pairs if expected == "violates"]
    mutation_hits = [got for _, got in mutation_expected if got == "violates"]
    return SplitScore(
        split=split,
        cases=len(cases),
        judged=judged,
        known_violation_detection=_rate(len(detected), len(expected_violations)),
        unresolved_rate=_rate(len(unresolved), judged),
        false_positive_rate=(flagged / len(cases)) if smoke_only else _rate(len(false_positives), len(clean)),
        noisy_candidate_rate=(noisy_cases / len(cases)) if smoke_only else _rate(len(noisy_candidates), len(quiet)),
        mutation_detection=_rate(len(mutation_hits), len(mutation_expected)),
        smoke_only=smoke_only,
        ok=not failures and (flagged == 0 and noisy_cases == 0 if smoke_only else True),
        failures=failures,
    )


BENCHMARK_SPLITS = ("tuning", "regression", "gap-holdout", "smoke")


def run_benchmark(splits: list[str] | None = None) -> list[SplitScore]:
    wanted = list(splits) if splits is not None else list(BENCHMARK_SPLITS)
    grouped: dict[str, list[Case]] = {name: [] for name in wanted}
    unknown: list[str] = []
    for case in all_cases():
        if case.split in grouped:
            grouped[case.split].append(case)
        elif case.split not in BENCHMARK_SPLITS:
            unknown.append(f"{case.id}: split {case.split} is not in the inventory")
    scores = [score_split(name, grouped[name]) for name in wanted]
    if unknown:
        scores.append(
            SplitScore(
                split="inventory",
                cases=len(unknown),
                judged=0,
                known_violation_detection=None,
                unresolved_rate=None,
                false_positive_rate=None,
                noisy_candidate_rate=None,
                mutation_detection=None,
                smoke_only=False,
                ok=False,
                failures=unknown,
            )
        )
    return scores


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_scores(scores: list[SplitScore]) -> str:
    lines = ["# Spec-check benchmark", ""]
    for score in scores:
        label = " (smoke only — clean changes)" if score.smoke_only else ""
        lines.append(f"## {score.split}{label}")
        lines.append("")
        lines.append(f"- cases: {score.cases}")
        if score.smoke_only:
            lines.append(f"- false violations on clean changes: {_fmt(score.false_positive_rate)}")
            lines.append(f"- noisy candidate findings on clean changes: {_fmt(score.noisy_candidate_rate)}")
        else:
            lines.append(f"- judged claim rows: {score.judged}")
            lines.append(f"- known-violation detection (scored cases only): {_fmt(score.known_violation_detection)}")
            lines.append(f"- unresolved rate: {_fmt(score.unresolved_rate)}")
            lines.append(f"- false violations: {_fmt(score.false_positive_rate)}")
            lines.append(f"- noisy candidate findings: {_fmt(score.noisy_candidate_rate)}")
            lines.append(f"- mutation detection: {_fmt(score.mutation_detection)}")
        if score.failures:
            lines.append("- failures:")
            for failure in score.failures:
                lines.append(f"  - {failure}")
        lines.append("")
    return "\n".join(lines)
