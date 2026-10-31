"""`kypp project` and `kypp purge` over KYPP_URL print what the operator route answered."""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from kypp import remote, shell


def _run(argv, answers, main=shell.project_main, prog="kypp project"):
    calls = []
    def fake(op, **args):
        calls.append((op, args))
        return answers[op]
    out = io.StringIO()
    with mock.patch.object(sys, "argv", [prog, *argv]), mock.patch.object(remote, "call", fake), \
            mock.patch.dict(os.environ, {"KYPP_URL": "https://k.example"}), contextlib.redirect_stdout(out):
        main()
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


class PurgeCliTest(unittest.TestCase):
    def test_purges_each_handle_with_the_reason(self):
        calls, out = _run(["abcd1234", "ef567890", "--reason", "leaked key"],
                          {"purge": {"claim": {"id": "abcd1234" + "0" * 24}}}, shell.purge_main, "kypp purge")
        self.assertEqual(calls, [("purge", {"handle": "abcd1234", "reason": "leaked key"}),
                                 ("purge", {"handle": "ef567890", "reason": "leaked key"})])
        self.assertEqual(out.splitlines(), ["abcd1234 purged — leaked key"] * 2)

    def test_needs_a_reason(self):
        with mock.patch.object(sys, "argv", ["kypp purge", "abcd1234"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                shell.purge_main()


if __name__ == "__main__":
    unittest.main()
