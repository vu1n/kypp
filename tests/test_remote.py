"""The CLI's hosted backend never follows a redirect with the operator token."""
import http.server
import os
import threading
import unittest
from unittest import mock

from kypp import remote


class _Redirect(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        self.seen.append(("POST", self.path, self.headers.get("Authorization")))
        self.send_response(302)
        self.send_header("Location", "/elsewhere")
        self.end_headers()

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"claims": []}')

    def log_message(self, *a):
        pass


class RemoteTest(unittest.TestCase):
    def test_a_redirect_is_refused_not_followed(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Redirect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        env = {"KYPP_URL": f"http://127.0.0.1:{srv.server_port}", "KYPP_OPERATOR_TOKEN": "secret-token"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(SystemExit) as e:
                remote.call("verifiable", project="p")
        self.assertIn("302", str(e.exception))
        self.assertEqual([m for m, _, _ in _Redirect.seen], ["POST"])

    def test_plain_http_is_refused_except_to_this_machine(self):
        for url in ("http://kypp.example.com", "kypp.example.com", "ftp://kypp.example.com"):
            with mock.patch.dict(os.environ, {"KYPP_URL": url, "KYPP_OPERATOR_TOKEN": "t"}):
                with self.assertRaises(SystemExit) as e:
                    remote.call("recall", query="x")
                self.assertIn("https", str(e.exception))

    def test_no_token_exits_before_any_request(self):
        with mock.patch.dict(os.environ, {"KYPP_URL": "http://127.0.0.1:9"}, clear=False):
            os.environ.pop("KYPP_OPERATOR_TOKEN", None)
            with self.assertRaises(SystemExit):
                remote.call("recall", query="x")


if __name__ == "__main__":
    unittest.main()
