"""Offline mode for the hosted backend: only an unreachable Worker falls back to the cache or queue,
and a queued write leaves the queue only after the Worker answered it."""
import contextlib
import http.server
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

from kypp import offline, remote, shell


def _claim(i, status="accepted", **over):
    return {"id": f"{i:08x}" + "0" * 24, "type": "fact", "subject": f"subject {i}", "content": f"lesson number {i}",
            "scope": "project", "project": "p", "status": status, "authority": "agent", "confidence": 0.7,
            "updated_at": f"2026-11-0{i}T00:00:00Z", **over}


class _Worker(http.server.BaseHTTPRequestHandler):
    claims: list = []
    seen: list = []
    refuse: set = set()  # ops answered 400
    page = 2

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seen.append(body)
        if self.headers.get("Authorization") != "Bearer tok":
            return self._send(401, {"error": "operator token required"})
        if body["op"] in self.refuse:
            return self._send(400, {"error": f"{body['op']} refused"})
        if body["op"] == "snapshot":
            rest = [c for c in self.claims if c["id"] > body.get("after", "")]
            got = rest[:self.page]
            return self._send(200, {"claims": got, "next": got[-1]["id"] if len(rest) > self.page else None})
        if body["op"] == "remember":
            return self._send(200, {"id": "f" * 32, "status": "candidate"})
        return self._send(200, {"text": "online answer"})

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def _dead_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def _run(main, *argv) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["kypp", *argv]), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        main()
    return out.getvalue(), err.getvalue()


class OfflineTest(unittest.TestCase):
    def setUp(self):
        _Worker.claims = [_claim(1), _claim(2, status="candidate"), _claim(3, type="pitfall")]
        _Worker.seen, _Worker.refuse, _Worker.page = [], set(), 2
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _Worker)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.tmp = tempfile.mkdtemp()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.env = {"KYPP_URL": self.url, "KYPP_OPERATOR_TOKEN": "tok", "KYPP_PROJECT": "p",
                    "KYPP_CACHE_DB": os.path.join(self.tmp, "offline.db")}

    def env_at(self, url, **over):
        return mock.patch.dict(os.environ, {**self.env, "KYPP_URL": url, **over})

    def sync(self):
        with self.env_at(self.url):
            return _run(offline.sync_main)

    def test_sync_pages_the_whole_project_and_offline_reads_answer_from_it(self):
        out, _ = self.sync()
        self.assertIn("cached 3 claim(s)", out)
        dead = _dead_url()
        # The cache is keyed by Worker URL; point the dead URL's key at the same rows.
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE claims SET url = ?", (dead,))
            db.execute("UPDATE synced SET url = ?", (dead,))
            db.commit()
        with self.env_at(dead):
            out, err = _run(shell.recall_main, "lesson", "--candidates")
            self.assertIn("offline", err)
            self.assertEqual(len(out.strip().splitlines()), 3)
            out, _ = _run(shell.recall_main, "lesson")
            self.assertNotIn("subject 2", out)  # candidates only with --candidates
            out, _ = _run(shell.briefing_main)
            self.assertTrue(out.startswith(_claim(3)["id"][:8]))  # pitfalls first
            out, _ = _run(shell.show_main, _claim(1)["id"][:8])
            self.assertEqual(json.loads(out)["subject"], "subject 1")

    def test_an_error_answer_never_falls_back(self):
        self.sync()
        with self.env_at(self.url, KYPP_OPERATOR_TOKEN="wrong"):
            with self.assertRaises(SystemExit) as e:
                _run(shell.recall_main, "lesson")
            self.assertIn("401", str(e.exception.code))
            with self.assertRaises(SystemExit):
                _run(shell.remember_main, "s", "c")
        with self.env_at(self.url):
            self.assertEqual(offline._queued(offline._db(), "p"), [])

    def test_no_cache_keeps_the_cant_reach_error(self):
        with self.env_at(_dead_url()):
            with self.assertRaises(SystemExit) as e:
                _run(shell.recall_main, "lesson")
        self.assertIn("can't reach", str(e.exception.code))
        self.assertIn("kypp sync", str(e.exception.code))

    def test_queued_writes_leave_the_queue_only_once_the_worker_answers(self):
        dead = _dead_url()
        with self.env_at(dead):
            out, _ = _run(shell.remember_main, "a", "first")
            self.assertIn("queued", out)
            _run(shell.remember_main, "b", "second")
            with self.assertRaises(SystemExit) as e:
                _run(shell.correct_main, "a", "human answer")
            self.assertIn("not queued offline", str(e.exception.code))
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
            self.assertIn("2 write(s) stay queued", str(e.exception.code))
            db = offline._db()
            db.execute("UPDATE outbox SET url = ?", (self.url,))
            db.commit()
        _Worker.refuse = {"remember"}
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("1 after it stay queued", str(e.exception.code))
        _Worker.refuse = set()
        out, _ = self.sync()
        self.assertIn("sent 2 queued write(s)", out)
        sent = [b for b in _Worker.seen if b["op"] == "remember"]
        self.assertEqual([b["subject"] for b in sent], ["a", "a", "b"])  # refused once, then each once
        self.assertNotIn("correct", [b["op"] for b in _Worker.seen])
        out, _ = self.sync()
        self.assertIn("sent 0 queued write(s)", out)

    def test_a_failed_pull_keeps_the_previous_copy(self):
        self.sync()
        _Worker.claims = [_claim(4), _claim(5), _claim(6)]
        _Worker.refuse = set()
        orig = remote.call
        calls = []

        def flaky(op, **args):
            calls.append(op)
            if op == "snapshot" and calls.count("snapshot") == 2:
                raise SystemExit("kypp: boom")
            return orig(op, **args)

        with self.env_at(self.url), mock.patch.object(remote, "call", flaky):
            with self.assertRaises(SystemExit):
                _run(offline.sync_main)
            ids = sorted(c["id"] for c in offline._cached(offline._db(), "p"))
        self.assertEqual(ids, sorted(_claim(i)["id"] for i in (1, 2, 3)))


if __name__ == "__main__":
    unittest.main()
