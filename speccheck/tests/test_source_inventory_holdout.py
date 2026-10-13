"""Frozen independent CLI holdout; no loader imports or injected read faults."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# Tokens are split so repository scans do not consume fixture references.
MISSING_REF = "doc:" + "//kypp/holdout-missing@0042#retained"
ROOT = Path(__file__).resolve().parents[2]

# label, mode, literal path, operation, expected outcome
CASES = (
    ("commit-space", "commit", "kypp/space name.py", "reference", "violation"),
    ("worktree-tab", "worktree", "worker/src/tab\tname.ts", "reference", "violation"),
    ("commit-newline", "commit", "docs/line\nbreak.md", "reference", "violation"),
    ("worktree-unicode", "worktree", ".github/workflows/雪.yml", "reference", "violation"),
    ("commit-glob-literal", "commit", "worker/migrations/[ab]*?.sql", "reference", "violation"),
    ("worktree-brief-quote", "worktree", '.brief/notes/quote"\\name.txt', "reference", "violation"),
    ("commit-invalid-utf8", "commit", "docs/invalid.md", "invalid", "error"),
    ("worktree-invalid-utf8", "worktree", "kypp/invalid.py", "invalid", "error"),
    ("commit-symlink", "commit", "docs/link.md", "symlink", "error"),
    ("worktree-symlink", "worktree", "worker/src/link.ts", "symlink", "error"),
    ("worktree-intentional-deletion", "worktree", "docs/deleted.md", "delete", "clean"),
    ("outside-prefix-controls", "worktree", "vendor/invalid.dat", "outside", "clean"),
)


class SourceInventoryHoldout(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                text=True, check=True)
        return result.stdout.strip()

    def check_case(self, case) -> None:
        label, mode, literal, operation, expected = case
        with tempfile.TemporaryDirectory(prefix="source-holdout-") as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.name", "Holdout")
            self.git(repo, "config", "user.email", "holdout@example.invalid")
            (repo / "README.md").write_text("Clean baseline.\n", encoding="utf-8")
            self.git(repo, "add", "--", ".")
            self.git(repo, "commit", "-qm", "baseline")
            baseline = self.git(repo, "rev-parse", "HEAD")
            path = repo / literal
            path.parent.mkdir(parents=True, exist_ok=True)
            if operation in {"reference", "delete"}:
                path.write_text("# preserved sentinel: " + MISSING_REF + "\n", encoding="utf-8")
            elif operation == "invalid":
                path.write_bytes(("# " + MISSING_REF + "\n").encode() + b"\xff")
            elif operation == "symlink":
                target = Path(temp) / "external.txt"
                target.write_text(MISSING_REF + "\n", encoding="utf-8")
                path.symlink_to(target)
            else:
                path.write_bytes(b"\xff")
                (repo / "vendor/link").symlink_to(Path(temp) / "absent")
                (repo / "docs-neighbor").mkdir()
                (repo / "docs-neighbor/invalid.md").write_bytes(b"\xff")
            # Stage each case to exercise literal Git inventory, including added worktree files.
            self.git(repo, "add", "--", ".")
            if mode == "commit" or operation == "delete":
                self.git(repo, "commit", "-qm", label)
            if operation == "delete":
                path.unlink()
            report = Path(temp) / "report.md"
            json_report = Path(temp) / "report.json"
            cache = Path(temp) / "cache"
            command = [sys.executable, "-m", "speccheck", "check", "--repo", str(repo),
                       "--baseline", baseline, "--out", str(report), "--json", str(json_report),
                       "--cache", str(cache)]
            if mode == "commit":
                command += ["--head", "HEAD"]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                                    timeout=30)
            if expected == "error":
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("speccheck:", result.stderr)
                self.assertFalse(report.exists())
                self.assertFalse(json_report.exists())
                self.assertFalse(cache.exists() and any(cache.rglob("*")))
                self.assertNotIn("# Spec", result.stdout)
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(report.exists())
            data = json.loads(json_report.read_text(encoding="utf-8"))
            references = [v for v in data["verdicts"] if v["rule_id"] == "doc-refs"]
            self.assertTrue(references, data)
            if expected == "violation":
                bad = [v for v in references if v["status"] == "violates"]
                self.assertTrue(bad, references)
                self.assertTrue(any(MISSING_REF in (v.get("counterexample") or "") for v in bad), bad)
                # Literal path evidence cannot be silently quoted, split, or glob-expanded.
                self.assertTrue(any(literal in (v.get("counterexample") or "") for v in bad), bad)
            else:
                self.assertTrue(all(v["status"] == "conforms" for v in references), references)
                self.assertNotIn(MISSING_REF, report.read_text(encoding="utf-8"))


def _make_test(case):
    def test(self):
        self.check_case(case)
    return test


for _case in CASES:
    setattr(SourceInventoryHoldout, "test_" + _case[0].replace("-", "_"), _make_test(_case))


if __name__ == "__main__":
    unittest.main()
