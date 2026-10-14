"""The pinned authority-order bullet is not a clean single-writer claim."""

import unittest
from pathlib import Path

from speccheck.engine import run_check
from speccheck.evaluate import AUTH6_BULLETS, AUTH_BULLETS, DELETE_BULLETS, GRADER_BULLETS, SCHEMA_BULLETS, VERIFY_BULLETS
from speccheck.fixtures import clean_tree
from speccheck.gitio import load_files, rev_parse
from speccheck.textscan import group_bullets, invariant_bullets, split_front_matter

ROOT = Path(__file__).resolve().parents[2]


def _bullets(path: Path) -> list[str]:
    _, body = split_front_matter(path.read_text())
    return invariant_bullets(body)


def _coverage_sites(coverage: str, label: str) -> dict[str, list[int]]:
    """Path → line numbers named by one coverage label. Line numbers drift; the sites do not."""
    found: dict[str, list[int]] = {}
    marker = label + " "
    for piece in coverage.split(";"):
        piece = piece.strip()
        if not piece.startswith(marker):
            continue
        loc = piece[len(marker):]
        path, _, line = loc.rpartition(":")
        found.setdefault(path, []).append(int(line))
    return found


class AdjoiningTest(unittest.TestCase):
    def test_pins_match_ratified_specs(self):
        self.assertEqual(_bullets(ROOT / ".brief/docs/verify-operator-only/versions/v0002.md"), VERIFY_BULLETS)
        self.assertEqual(_bullets(ROOT / ".brief/docs/authority-order/versions/v0004.md"), AUTH_BULLETS)
        self.assertEqual(_bullets(ROOT / ".brief/docs/authority-order/versions/v0006.md"), AUTH6_BULLETS)
        self.assertEqual(_bullets(ROOT / ".brief/docs/additive-migrations/versions/v0002.md"), SCHEMA_BULLETS)
        self.assertEqual(_bullets(ROOT / ".brief/docs/append-only-history/versions/v0001.md"), DELETE_BULLETS)
        self.assertEqual(_bullets(ROOT / ".brief/docs/no-grader-leak/versions/v0001.md"), GRADER_BULLETS)

    def test_post_sync_not_clean_single_writer(self):
        bullets = _bullets(ROOT / ".brief/docs/authority-order/versions/v0004.md")
        isolated = bullets[1]
        self.assertIn("shell.correct_main", isolated)
        self.assertNotIn("POST /sync", isolated)
        groups = group_bullets(bullets, doc_id="authority-order", version="0004", anchor="authority-dominates")
        hits = [g for g in groups if "shell.correct_main" in g.text]
        self.assertTrue(hits)
        for group in hits:
            self.assertIn("POST /sync", group.text)
            self.assertIs(group.clean_single_writer, False)
            self.assertNotEqual(group.text, isolated)
        self.assertFalse(any(g.clean_single_writer is True for g in groups))

    def test_report_does_not_call_correct_main_the_only_writer(self):
        files, paths = load_files(ROOT, None)
        sha = rev_parse(ROOT, "HEAD")
        report = run_check(files, files, baseline_sha=sha, head_sha=sha + "-head", baseline_paths=paths, head_paths=paths)
        writer = next(v for v in report.verdicts if v.rule_id == "human-authority-writers")
        self.assertEqual(writer.status, "unresolved")
        self.assertIs(writer.clean_single_writer, False)
        self.assertIn("POST /sync", writer.claim_text)
        self.assertNotIn("is the only caller that writes", writer.claim_text)
        self.assertFalse(
            any(v.clean_single_writer is True and "shell.correct_main" in v.claim_text for v in report.verdicts)
        )


class LiveRepoTest(unittest.TestCase):
    def test_live_repo_has_no_violations(self):
        files, paths = load_files(ROOT, None)
        sha = rev_parse(ROOT, "HEAD")
        report = run_check(files, files, baseline_sha=sha, head_sha=sha + "-live", baseline_paths=paths, head_paths=paths)
        self.assertEqual(report.by_status("violates"), [])
        quiet = {v.rule_id for v in report.by_status("no_counterexample")}
        self.assertIn("mcp-no-verify-argument", quiet)
        self.assertIn("mcp-no-human-or-verified-authority", quiet)
        self.assertIn("authority-rank-order", quiet)
        self.assertIn("schema-add-column", quiet)
        bounded = {v.rule_id for v in report.by_status("bounded")}
        self.assertIn("doc-refs", bounded)
        self.assertIn("tools-initializer-no-verify", bounded)
        tools = next(v for v in report.verdicts if v.rule_id == "tools-initializer-no-verify")
        self.assertEqual(tools.detail, "the fully parsed TOOLS initializer declares no verify property")
        writer = next(v for v in report.verdicts if v.rule_id == "verified-authority-writer")
        self.assertEqual(writer.status, "unresolved")
        self.assertEqual(writer.spec, "doc://kypp/authority-order@0006#authority-dominates")
        self.assertIn("mark_verified", writer.claim_text)
        self.assertIn("not classified", writer.detail)
        # Dynamic authority SQL stays unresolved. The cited line is the start of the statement;
        # the interpolation may sit a couple of lines later, so match the statement, not a fixed number.
        sites = _coverage_sites(writer.coverage, "dynamic authority SQL")
        store_py = (ROOT / "kypp/store.py").read_text().splitlines()
        self.assertEqual(len(sites["kypp/store.py"]), 1)
        claim_sql = "\n".join(store_py[sites["kypp/store.py"][0] - 1:][:4])
        self.assertIn("INSERT INTO memory_claims", claim_sql)
        self.assertIn("{emb_sql}", claim_sql)
        store_ts = (ROOT / "worker/src/store.ts").read_text().splitlines()
        self.assertEqual(len(sites["worker/src/store.ts"]), 2)
        for line in sites["worker/src/store.ts"]:
            stmt = store_ts[line - 1]
            self.assertIn("UPDATE memory_claims", stmt)
            self.assertIn("${UNSORTED}", stmt)
        self.assertIsNone(writer.counterexample)
        deleted = next(v for v in report.verdicts if v.rule_id == "no-delete-claims")
        self.assertEqual(deleted.status, "unresolved")
        self.assertIn("coverage worker/src/store.ts", deleted.coverage)
        self.assertNotIn("dynamic DELETE", deleted.coverage)
        self.assertNotIn("dynamic DELETE", deleted.detail)
        grader = next(v for v in report.verdicts if v.rule_id == "no-grader-verbatim")
        self.assertEqual(grader.status, "unresolved")
        stale = [a for a in report.advisories if a.code == "stale-pin"]
        pins = [a.detail.split(" resolves", 1)[0] for a in stale]
        self.assertEqual(len(pins), len(set(pins)))
        timeout = next(v for v in report.verdicts if v.rule_id == "verify-timeout-behaviour")
        self.assertEqual(timeout.status, "unresolved")
        for verdict in report.verdicts:
            self.assertNotIn(verdict.severity, {"P0", "P1", "P2"})


if __name__ == "__main__":
    unittest.main()
