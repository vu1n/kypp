"""run_check("sleep 30", timeout=0.4) returns failure and a timeout message within five seconds.

This does not prove that every descendant process is dead, that a failed spawn is handled,
or that a sweep continues on to later claims.
"""

from __future__ import annotations

import time
import unittest

from kypp.verify import run_check


class VerifyTimeoutTest(unittest.TestCase):
    def test_sleep_returns_failure_and_timeout_message_within_five_seconds(self):
        started = time.monotonic()
        passed, output = run_check("sleep 30", cwd="/tmp", timeout=0.4)
        elapsed = time.monotonic() - started
        self.assertFalse(passed)
        self.assertIn("timed out", output)
        self.assertLess(elapsed, 5)


if __name__ == "__main__":
    unittest.main()
