"""Run the advisory check. Baseline spec is the rule source; head spec is not."""

from __future__ import annotations

import sys

from .cache import Cache
from .evaluate import CODE_REVISION, rule_verdicts, unsupported_verdicts
from .model import Report
from .specindex import load_spec
from .structural import (
    alias_verdict,
    amendments_between,
    build_reference_index,
    dependency_hash,
    doc_ref_verdicts,
    feature_path_verdict,
    overlap_advisories,
    signoff_verdict,
)


def run_check(
    baseline: dict[str, str],
    head: dict[str, str],
    *,
    baseline_sha: str,
    head_sha: str,
    baseline_paths: list[str] | None = None,
    head_paths: list[str] | None = None,
    cache: Cache | None = None,
    notes: list[str] | None = None,
) -> Report:
    cache = cache or Cache()
    head_paths = head_paths if head_paths is not None else sorted(head)
    spec = load_spec(baseline)
    notes = list(notes or [])
    notes.append(f"registry code revision {CODE_REVISION}")
    notes.append(f"environment python {sys.version.split()[0]}")
    notes.append("rule text comes from the baseline spec; head spec edits are amendments, not the rules")

    def build() -> dict:
        return build_reference_index(baseline, head)

    payload = cache.references(head_sha, baseline_sha, dependency_hash(baseline, head), build)
    ref_verdict, ref_advisories = doc_ref_verdicts(payload)
    verdicts = [ref_verdict, alias_verdict(spec, baseline)]
    signoff = signoff_verdict(head, spec)
    if signoff is not None:
        verdicts.append(signoff)
    features = feature_path_verdict(head, head_paths)
    if features is not None:
        verdicts.append(features)
    rule_rows, covered = rule_verdicts(spec, head, cache)
    verdicts.extend(rule_rows)
    verdicts.extend(unsupported_verdicts(spec, covered))
    order = {
        "violates": 0,
        "candidate": 1,
        "unresolved": 2,
        "unsupported": 3,
        "no_counterexample": 4,
        "bounded": 5,
    }
    verdicts.sort(key=lambda v: (order.get(v.status, 9), v.rule_id))
    advisories = ref_advisories + overlap_advisories(spec, head_paths)
    return Report(
        baseline_sha=baseline_sha,
        head_sha=head_sha,
        verdicts=verdicts,
        advisories=advisories,
        amendments=amendments_between(baseline, head),
        notes=notes,
        cache=cache.stats(),
    )
