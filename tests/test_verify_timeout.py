"""A hanging verifier is killed and reported as a failed check."""

from __future__ import annotations

import time
import unittest

from kypp.verify import run_check


class VerifyTimeoutTest(unittest.TestCase):
    def test_hanging_shell_is_killed_and_failed(self):
        started = time.monotonic()
        passed, output = run_check("sleep 30", cwd="/tmp", timeout=0.4)
        elapsed = time.monotonic() - started
        self.assertFalse(passed)
        self.assertIn("timed out", output)
        self.assertLess(elapsed, 5)


if __name__ == "__main__":
    unittest.main()
