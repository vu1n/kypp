"""The checker never writes .brief or SIGNOFF."""

import tempfile
import unittest
from pathlib import Path

from speccheck.cli import main
from speccheck.engine import run_check
from speccheck.fixtures import clean_tree


class NoWriteTest(unittest.TestCase):
    def test_run_check_does_not_create_brief_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            files = clean_tree()
            files[".brief/SIGNOFF"] = "operator-only-verify conforms: already true\n"
            for path, text in files.items():
                dest = repo / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text)
            before = {p: p.read_bytes() for p in (repo / ".brief").rglob("*") if p.is_file()}
            run_check(files, files, baseline_sha="base", head_sha="head")
            after = {p: p.read_bytes() for p in (repo / ".brief").rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_cli_refuses_to_write_the_report_under_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".brief").mkdir()
            code = main(["check", "--repo", str(repo), "--out", str(repo / ".brief" / "out.md")])
            self.assertEqual(code, 1)
            self.assertFalse((repo / ".brief" / "out.md").exists())


if __name__ == "__main__":
    unittest.main()
