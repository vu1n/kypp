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
import urllib.error
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
    claim_reply: dict = {}

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
        if body["op"] in ("purge", "reject"):
            return self._send(200, self.claim_reply)
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

    def offline_copy(self, dead):
        """Sync online, then file the copy under the dead URL so the CLI finds it while offline."""
        self.sync()
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE synced SET url = ?", (dead,))
            db.execute("UPDATE claims SET url = ?", (dead,))
            db.commit()

    def back_online(self):
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE outbox SET url = ?", (self.url,))
            db.commit()

    def test_offline_remember_needs_a_synced_project(self):
        with self.env_at(_dead_url()):
            with self.assertRaises(SystemExit) as e:
                _run(shell.remember_main, "a", "first")
            self.assertIn("not queued", str(e.exception.code))
            self.assertEqual(offline._queued(offline._db(), "p"), [])

    def test_queued_writes_leave_the_queue_only_once_the_worker_answers(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            out, _ = _run(shell.remember_main, "a", "first")
            self.assertIn("queued", out)
            _run(shell.remember_main, "b", "second")
            out, _ = _run(shell.recall_main, "first")
            self.assertIn("(queued)", out)
            with self.assertRaises(SystemExit) as e:
                _run(shell.correct_main, "a", "human answer")
            self.assertIn("not queued offline", str(e.exception.code))
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
            self.assertIn("2 write(s) stay queued", str(e.exception.code))
        self.back_online()
        _Worker.refuse = {"remember"}
        _Worker.claims = [_claim(4)]
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("refused queued write #1", str(e.exception.code))
        self.assertIn("cached 1 claim(s)", str(e.exception.code))  # a stuck write doesn't freeze the copy
        _Worker.refuse = set()
        out, _ = self.sync()
        self.assertIn("sent 2 queued write(s)", out)
        sent = [b for b in _Worker.seen if b["op"] == "remember"]
        self.assertEqual([b["subject"] for b in sent], ["a", "a", "b"])  # refused once, then each once
        self.assertNotIn("correct", [b["op"] for b in _Worker.seen])
        out, _ = self.sync()
        self.assertIn("sent 0 queued write(s)", out)

    def test_a_refused_write_can_be_dropped(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        _Worker.refuse = {"remember"}
        with self.assertRaises(SystemExit):
            self.sync()
        with self.env_at(self.url):
            out, _ = _run(offline.sync_main, "--drop", "1")
        self.assertIn("dropped queued write #1", out)
        self.assertIn("sent 0 queued write(s)", out)

    def test_a_write_whose_answer_was_lost_is_not_sent_twice(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        orig = remote.call

        def lost(op, **args):
            res = orig(op, **args)
            if op == "remember":  # applied, then the answer never arrives
                _Worker.claims.append(_claim(9, subject=args["subject"], content=args["content"]))
                raise SystemExit("kypp: timed out")
            return res

        with self.env_at(self.url), mock.patch.object(remote, "call", lost):
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
        self.assertIn("no answer for queued write #1", str(e.exception.code))
        out, _ = self.sync()
        self.assertIn("sent 1 queued write(s)", out)
        self.assertEqual(len([b for b in _Worker.seen if b["op"] == "remember"]), 1)

    def test_a_tampered_queue_row_never_becomes_another_operator_write(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE outbox SET args = json_set(args, '$.authority', 'human')")
            db.execute("INSERT INTO outbox(url, project, op, args, queued_at) VALUES(?, 'p', 'purge', '{}', 'x')",
                       (self.url,))
            db.commit()
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("never queued", str(e.exception.code))
        sent = [b for b in _Worker.seen if b["op"] not in ("snapshot",)]
        self.assertEqual([b["op"] for b in sent], ["remember"])
        self.assertNotIn("authority", sent[0])

    def test_a_cursor_that_does_not_advance_is_refused(self):
        orig = remote.call

        def stuck(op, **args):
            res = orig(op, **args)
            return {**res, "next": "0"} if op == "snapshot" else res

        with self.env_at(self.url), mock.patch.object(remote, "call", stuck):
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
        self.assertIn("did not advance", str(e.exception.code))

    def test_a_tls_failure_is_not_offline(self):
        import ssl
        import urllib.error
        err = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        with self.env_at("https://kypp.example"), mock.patch.object(remote._opener, "open", side_effect=err):
            with self.assertRaises(SystemExit) as e:
                remote.call("recall", query="x")
        self.assertNotIsInstance(e.exception, remote.Unreachable)
        self.assertIn("TLS", str(e.exception.code))

    def test_a_refused_write_is_not_mistaken_for_an_applied_one(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "subject 1", "lesson number 1", "--accept")
        self.back_online()
        _Worker.refuse = {"remember"}
        with self.assertRaises(SystemExit):
            self.sync()
        _Worker.refuse = set()
        _Worker.claims[0] = {**_Worker.claims[0], "status": "candidate"}  # same text, but not what was asked
        self.sync()
        self.assertEqual(len([b for b in _Worker.seen if b["op"] == "remember"]), 2)

    def test_a_lost_answer_is_re_sent_when_the_hosted_claim_differs(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "subject 1", "lesson number 1", "--type", "pitfall")
        self.back_online()
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE outbox SET sent_at = 'earlier'")
            db.commit()
        self.sync()  # the hosted "subject 1" is a fact, so this pitfall was never applied
        self.assertEqual(len([b for b in _Worker.seen if b["op"] == "remember"]), 1)

    def test_purge_and_reject_drop_the_cached_copy(self):
        self.sync()
        for op, cid in (("purge", _claim(1)["id"]), ("reject", _claim(3)["id"])):
            _Worker.claim_reply = {"claim": {"id": cid, "status": "rejected", "type": "fact", "subject": "s"}}
            with self.env_at(self.url):
                if op == "purge":
                    _run(shell.purge_main, cid[:8], "--reason", "leaked")
                else:
                    _run(shell.reject_main, cid[:8])
        with self.env_at(self.url):
            left = [c["id"] for c in offline._cached(offline._db(), "p")]
        self.assertEqual(left, [_claim(2)["id"]])

    def test_only_one_sync_replays_at_a_time(self):
        import fcntl
        self.sync()
        with self.env_at(self.url):
            held = open(offline._path() + ".lock", "w")
            fcntl.flock(held, fcntl.LOCK_EX)
            self.addCleanup(held.close)
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
        self.assertIn("another `kypp sync` is running", str(e.exception.code))

    def test_a_failure_while_sending_is_not_offline(self):
        def send(conn, data):
            if conn.sock is None:
                conn.connect()  # connected, then the pipe breaks mid-request
            raise BrokenPipeError("pipe")

        with self.env_at(self.url), mock.patch.object(remote._HTTPConnection, "send", send):
            with self.assertRaises(SystemExit) as e:
                remote.call("remember", subject="s", content="c", project="p")
        self.assertNotIsInstance(e.exception, remote.Unreachable)
        self.assertIn("while sending", str(e.exception.code))

    def test_a_purge_during_a_pull_stays_purged(self):
        self.sync()
        orig = remote.call

        def purge_mid_pull(op, **args):
            res = orig(op, **args)
            if op == "snapshot" and not args.get("after"):
                offline.forget(_claim(1)["id"])  # another process purges after this page was fetched
            return res

        with self.env_at(self.url), mock.patch.object(remote, "call", purge_mid_pull):
            _run(offline.sync_main)
            ids = [c["id"] for c in offline._cached(offline._db(), "p")]
        self.assertNotIn(_claim(1)["id"], ids)

    def test_a_lost_answer_with_out_of_range_confidence_is_not_sent_twice(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "subject 1", "lesson number 1", "--confidence", "2")
        self.back_online()
        _Worker.claims[0] = {**_Worker.claims[0], "status": "candidate", "confidence": 1}
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE outbox SET sent_at = 'earlier'")
            db.commit()
        self.sync()
        self.assertEqual(len([b for b in _Worker.seen if b["op"] == "remember"]), 0)

    def test_a_gateway_error_after_a_committed_write_is_not_sent_twice(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "subject 1", "lesson number 1")
        self.back_online()
        orig = remote.call

        def gateway(op, **args):
            if op == "remember":  # committed upstream, then the gateway answers 502
                _Worker.seen.append({"op": op, **args})
                raise SystemExit("kypp: answered 502: bad gateway")
            return orig(op, **args)

        with self.env_at(self.url), mock.patch.object(remote, "call", gateway):
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
        self.assertIn("no answer for queued write #1", str(e.exception.code))
        self.sync()  # the snapshot already holds it, so it isn't re-sent
        self.assertEqual(len([b for b in _Worker.seen if b["op"] == "remember"]), 1)

    def test_only_a_4xx_is_a_refusal(self):
        for code, refused in ((400, True), (401, True), (408, False), (502, False), (504, False)):
            err = urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))
            with self.env_at(self.url), mock.patch.object(remote._opener, "open", side_effect=err):
                with self.assertRaises(SystemExit) as e:
                    remote.call("remember", subject="s")
            self.assertEqual(isinstance(e.exception, remote.Refused), refused, code)

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
