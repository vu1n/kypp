"""`kypp project` is hosted-only and prints what the operator route answered."""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from kypp import remote, shell


def _run(argv, answers):
    calls = []
    def fake(op, **args):
        calls.append((op, args))
        return answers[op]
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["kypp project", *argv]), mock.patch.object(remote, "call", fake), \
            mock.patch.dict(os.environ, {"KYPP_URL": "https://k.example"}), contextlib.redirect_stdout(out):
        shell.project_main()
    return calls, out.getvalue()


class ProjectCliTest(unittest.TestCase):
    def test_add_registers_and_reports_filed_claims(self):
        calls, out = _run(["add", "grokbot", "--description", "research briefs"],
                          {"register": {"project": "grokbot", "created": True, "filed": 6}})
        self.assertEqual(calls, [("register", {"project": "grokbot", "description": "research briefs"})])
        self.assertEqual(out.strip(), "grokbot: registered, filed 6 waiting claim(s)")

    def test_list_prints_names(self):
        _, out = _run(["list"], {"projects": {"projects": [{"name": "kypp", "description": ""}, {"name": "tilt", "description": "game"}]}})
        self.assertEqual(out.splitlines(), ["kypp", "tilt — game"])

    def test_needs_a_hosted_store(self):
        with mock.patch.object(sys, "argv", ["kypp project", "list"]), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as e:
                shell.project_main()
        self.assertIn("KYPP_URL", str(e.exception))


if __name__ == "__main__":
    unittest.main()
