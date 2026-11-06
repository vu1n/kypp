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
    applied: dict = {}  # idempotency key -> claim id, as the Worker's operator_receipts
    old = False  # a Worker deployed before idempotency keys: no `capabilities`, `key` ignored


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
        if body["op"] == "capabilities":
            if self.old:
                return self._send(400, {"error": 'unknown op "capabilities"'})
            return self._send(200, {"idempotent_writes": True})
        if body["op"] == "remember" and self.old:
            self.applied[len(self.applied)] = body["subject"]
            return self._send(200, {"id": "e" * 32, "status": "candidate"})
        if body["op"] == "remember":
            replayed = body.get("key") in self.applied
            cid = self.applied.setdefault(body.get("key"), f"{len(self.applied):032x}")
            return self._send(200, {"id": cid, "status": "candidate", "key": body.get("key"), "replayed": replayed})
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
        _Worker.seen, _Worker.refuse, _Worker.page, _Worker.applied, _Worker.old = [], set(), 2, {}, False
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
            self.assertIn("2 write(s) stay queued", str(e.exception.code))  # unreachable: nothing marked
        self.back_online()
        out, _ = self.sync()
        self.assertIn("sent 2 queued write(s)", out)
        self.assertEqual([b["subject"] for b in _Worker.seen if b["op"] == "remember"], ["a", "b"])
        self.assertNotIn("correct", [b["op"] for b in _Worker.seen])
        out, _ = self.sync()
        self.assertIn("sent 0 queued write(s)", out)

    def test_a_write_without_a_200_is_sent_again_under_its_key_and_lands_once(self):
        # A refusal, a gateway error or a lost reply after a commit: the next sync resends the same key,
        # and the Worker returns the first claim instead of inserting another.
        for failure in ("refused", "lost"):
            with self.subTest(failure=failure):
                self.setUp()
                dead = _dead_url()
                self.offline_copy(dead)
                with self.env_at(dead):
                    _run(shell.remember_main, "a", "first")
                    _run(shell.remember_main, "b", "second")
                self.back_online()
                orig, fails = remote.call, [1]

                def flaky(op, **args):
                    if op == "remember" and fails:
                        fails.pop()
                        if failure == "lost":
                            orig(op, **args)  # committed; the answer never arrives
                        raise SystemExit("kypp: answered 400" if failure == "refused" else "kypp: timed out")
                    return orig(op, **args)

                with self.env_at(self.url), mock.patch.object(remote, "call", flaky):
                    with self.assertRaises(SystemExit) as e:
                        _run(offline.sync_main)
                self.assertIn("sends it again under the same key", str(e.exception.code))
                self.assertIn("cached", str(e.exception.code))  # the copy still refreshed
                with self.env_at(self.url):
                    self.assertEqual(len(offline._queued(offline._db(), "p")), 2)  # "b" waits behind "a"
                out, _ = self.sync()
                self.assertIn("sent 2 queued write(s)", out)
                sent = [b for b in _Worker.seen if b["op"] == "remember"]
                self.assertEqual([b["subject"] for b in sent], ["a", "a", "b"] if failure == "lost" else ["a", "b"])
                self.assertEqual(len({b["key"] for b in sent}), 2)  # a resend reuses its key
                self.assertEqual(len(_Worker.applied), 2)

    def test_a_row_sent_before_keys_waits_for_the_operator(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
            _run(shell.remember_main, "b", "second")
        self.back_online()
        with self.env_at(self.url):
            db = offline._db()
            db.execute("UPDATE outbox SET key = NULL")  # as a cache from before keys
            db.execute("UPDATE outbox SET sent_at = 'earlier' WHERE seq = 1")
            db.commit()
        for _ in range(2):
            with self.assertRaises(SystemExit) as e:
                self.sync()
            self.assertIn("without an idempotency key", str(e.exception.code))
            self.assertIn("--resend 1", str(e.exception.code))
        self.assertEqual([b for b in _Worker.seen if b["op"] == "remember"], [])
        with self.env_at(self.url):
            out, _ = _run(offline.sync_main, "--resend", "1")
        self.assertIn("sent 2 queued write(s)", out)  # an unsent row from before keys gets one
        self.assertTrue(all(b.get("key") for b in _Worker.seen if b["op"] == "remember"))

    def test_a_lost_reply_from_a_worker_without_keys_is_never_resent_on_its_own(self):
        # A Worker deployed before keys ignores `key`; a resend after a committed write would duplicate it.
        _Worker.old = True
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        orig, fails = remote.call, [1]

        def lost(op, **args):
            if op == "remember" and fails:
                fails.pop()
                orig(op, **args)  # committed; the answer never arrives
                raise SystemExit("kypp: timed out")
            return orig(op, **args)

        with self.env_at(self.url), mock.patch.object(remote, "call", lost):
            with self.assertRaises(SystemExit) as e:
                _run(offline.sync_main)
        self.assertIn("predates idempotency keys", str(e.exception.code))
        _Worker.old = False  # even once the Worker is upgraded, the unkeyed send stays the operator's call
        for _ in range(2):
            with self.assertRaises(SystemExit) as e:
                self.sync()
            self.assertIn("--resend 1", str(e.exception.code))
        self.assertEqual(len(_Worker.applied), 1)
        self.assertNotIn("key", [k for b in _Worker.seen if b["op"] == "remember" for k in b])

    def test_a_keyed_row_is_not_resent_to_a_worker_that_stopped_honouring_keys(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        _Worker.refuse = {"remember"}
        with self.assertRaises(SystemExit):
            self.sync()
        _Worker.refuse, _Worker.old = set(), True
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("no longer honours idempotency keys", str(e.exception.code))
        self.assertEqual(len([b for b in _Worker.seen if b["op"] == "remember"]), 1)

    def test_a_resend_that_cannot_connect_keeps_the_earlier_send_on_record(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        orig, fails = remote.call, [1]

        def lost(op, **args):
            if op == "remember" and fails:
                fails.pop()
                orig(op, **args)  # committed; the answer never arrives
                raise SystemExit("kypp: timed out")
            return orig(op, **args)

        with self.env_at(self.url), mock.patch.object(remote, "call", lost):
            with self.assertRaises(SystemExit):
                _run(offline.sync_main)

        def offline_resend(op, **args):
            if op == "remember":
                raise remote.Unreachable("kypp: can't reach it")
            return orig(op, **args)

        with self.env_at(self.url), mock.patch.object(remote, "call", offline_resend):
            with self.assertRaises(SystemExit):
                _run(offline.sync_main)
        _Worker.old = True  # the Worker is downgraded: the row was sent once, so it stays the operator's call
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("no longer honours idempotency keys", str(e.exception.code))
        self.assertEqual(len(_Worker.applied), 1)

    def test_a_row_sent_to_a_worker_without_keys_loses_its_key(self):
        # Keyed Worker, the send can't connect; downgraded Worker, the reply is lost; upgraded again.
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
        self.back_online()
        orig = remote.call

        def unreachable(op, **args):
            if op == "remember":
                raise remote.Unreachable("kypp: can't reach it")
            return orig(op, **args)

        with self.env_at(self.url), mock.patch.object(remote, "call", unreachable):
            with self.assertRaises(SystemExit):
                _run(offline.sync_main)
        _Worker.old = True

        def lost(op, **args):
            res = orig(op, **args)
            if op == "remember":
                raise SystemExit("kypp: timed out")
            return res

        with self.env_at(self.url), mock.patch.object(remote, "call", lost):
            with self.assertRaises(SystemExit):
                _run(offline.sync_main)
        _Worker.old = False
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("without an idempotency key", str(e.exception.code))
        self.assertEqual(len(_Worker.applied), 1)

    def test_a_cache_from_before_keys_gains_the_column(self):
        import sqlite3
        path = self.env["KYPP_CACHE_DB"]
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, project TEXT "
                    "NOT NULL, op TEXT NOT NULL, args TEXT NOT NULL, queued_at TEXT NOT NULL, sent_at TEXT)")
        old.execute("INSERT INTO outbox(url, project, op, args, queued_at) VALUES(?, 'p', 'remember', ?, 'x')",
                    (self.url, json.dumps({"subject": "old", "content": "row", "project": "p"})))
        old.commit()
        old.close()
        out, _ = self.sync()
        self.assertIn("sent 1 queued write(s)", out)

    def test_drop_discards_a_write_the_worker_keeps_refusing(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            _run(shell.remember_main, "a", "first")
            _run(shell.remember_main, "b", "second")
        self.back_online()
        _Worker.refuse = {"remember"}
        with self.assertRaises(SystemExit) as e:
            self.sync()
        self.assertIn("--drop 1", str(e.exception.code))
        _Worker.refuse = set()
        with self.env_at(self.url):
            out, _ = _run(offline.sync_main, "--drop", "1")
        self.assertIn("dropped queued write #1", out)
        self.assertIn("sent 1 queued write(s)", out)

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
        sent = [b for b in _Worker.seen if b["op"] not in ("snapshot", "capabilities")]
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
                offline.forget(_claim(1)["id"], purged=True)  # another process purges after this page was fetched
            return res

        with self.env_at(self.url), mock.patch.object(remote, "call", purge_mid_pull):
            _run(offline.sync_main)
            ids = [c["id"] for c in offline._cached(offline._db(), "p")]
        self.assertNotIn(_claim(1)["id"], ids)

    def test_a_rejected_claim_comes_back_when_the_worker_revives_it(self):
        self.sync()
        _Worker.claim_reply = {"claim": {"id": _claim(3)["id"], "status": "rejected", "type": "fact", "subject": "s"}}
        with self.env_at(self.url):
            _run(shell.reject_main, _claim(3)["id"][:8])
        self.sync()  # `kypp verify` passed in between: the Worker still serves it
        with self.env_at(self.url):
            ids = [c["id"] for c in offline._cached(offline._db(), "p")]
        self.assertIn(_claim(3)["id"], ids)

    def test_the_recall_limit_covers_queued_writes(self):
        dead = _dead_url()
        self.offline_copy(dead)
        with self.env_at(dead):
            for i in range(3):
                _run(shell.remember_main, f"queued {i}", "lesson queued")
            out, _ = _run(shell.recall_main, "lesson", "--candidates", "--limit", "4")
            self.assertEqual(len(out.strip().splitlines()), 4)
            out, _ = _run(shell.recall_main, "lesson", "--limit", "0")
            self.assertEqual(out.strip(), "(no matching memory)")

    def test_a_reject_during_a_pull_stays_out_until_a_later_sync(self):
        self.sync()
        orig = remote.call

        def reject_mid_pull(op, **args):
            res = orig(op, **args)
            if op == "snapshot" and not args.get("after"):
                offline.forget(_claim(1)["id"], purged=False)  # another process rejects mid-pull
            return res

        with self.env_at(self.url), mock.patch.object(remote, "call", reject_mid_pull):
            _run(offline.sync_main)
            self.assertNotIn(_claim(1)["id"], [c["id"] for c in offline._cached(offline._db(), "p")])
        self.sync()  # a fresh snapshot that still holds it (revived by verify) brings it back
        with self.env_at(self.url):
            self.assertIn(_claim(1)["id"], [c["id"] for c in offline._cached(offline._db(), "p")])

    def test_show_finds_a_handle_from_any_synced_project(self):
        with self.env_at(self.url):
            _run(offline.sync_main, "--project", "q")
            db = offline._db()
            db.execute("UPDATE claims SET url = ?", (dead := _dead_url(),))
            db.execute("UPDATE synced SET url = ?", (dead,))
            db.commit()
        with self.env_at(dead):  # KYPP_PROJECT is p; the handle came from q's copy
            out, _ = _run(shell.show_main, _claim(2)["id"][:8])
        self.assertEqual(json.loads(out)["subject"], "subject 2")

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
