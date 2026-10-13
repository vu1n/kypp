"""Head code is checked against the base branch spec, including from the CLI."""

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from speccheck.cli import main
from speccheck.engine import run_check
from speccheck.fixtures import clean_tree, with_pr22


def _write(repo: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        dest = repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)


class AuthorityTest(unittest.TestCase):
    def test_engine_uses_baseline_spec_not_head_spec(self):
        baseline = clean_tree()
        head = with_pr22(baseline)
        head[".brief/docs/verify-operator-only/versions/v0002.md"] = head[
            ".brief/docs/verify-operator-only/versions/v0002.md"
        ].replace("never accept a `verify` argument", "may accept a `verify` argument", 1)
        report = run_check(baseline, head, baseline_sha="main", head_sha="pr")
        verdict = next(v for v in report.verdicts if v.rule_id == "mcp-no-verify-argument")
        self.assertEqual(verdict.status, "violates")
        self.assertTrue(report.amendments)
        self.assertIn("never accept", verdict.claim_text)
        self.assertNotIn("may accept", verdict.claim_text)

    def test_cli_rejects_head_as_its_own_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            _write(repo, clean_tree())
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "change"], cwd=repo, check=True, capture_output=True)
            relaxed = with_pr22(clean_tree())
            relaxed[".brief/docs/verify-operator-only/versions/v0002.md"] = relaxed[
                ".brief/docs/verify-operator-only/versions/v0002.md"
            ].replace("never accept a `verify` argument", "may accept a `verify` argument", 1)
            _write(repo, relaxed)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "relax"], cwd=repo, check=True, capture_output=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["check", "--repo", str(repo), "--baseline", "HEAD", "--head", "HEAD"])
            self.assertEqual(code, 0)
            self.assertIn("not judged against its own rules", buf.getvalue())
            self.assertIn("mcp-no-verify-argument", buf.getvalue())
            self.assertIn("[high]", buf.getvalue())
            # The report was printed. Re-run capturing via the engine path is not available;
            # invoke main again is enough if we inspect by calling the same resolution.
            from speccheck.gitio import resolve_baseline, rev_parse

            head = rev_parse(repo, "HEAD")
            baseline, note = resolve_baseline(repo, "HEAD", head)
            main_sha = rev_parse(repo, "main")
            self.assertEqual(baseline, main_sha)
            self.assertIsNotNone(note)
            self.assertNotEqual(baseline, head)


if __name__ == "__main__":
    unittest.main()
