import io
import json
import ssl
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import native
import remote_client as client


def packet():
    snapshot = native.Snapshot()
    snapshot.cash[:] = [1500, 1500]
    snapshot.jail[:] = [-1, -1]
    snapshot.owner[:] = [-1] * 40
    snapshot.houses, snapshot.hotels = 32, 12
    query = native.Request(0, 1, 60, -1, -1, 0)
    return native.make_packet(snapshot, 0, query, "match-1", 1)


class Response(io.BytesIO):
    def __init__(self, status, body=None, headers=None):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status, self.headers = status, headers or {}


class Transport:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        item = next(self.replies)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(code, headers=None):
    return HTTPError("https://competition.example/next", code, "error", headers or {}, io.BytesIO())


class RemoteClientTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(poll_url="https://competition.example/next",
                                    submit_url="https://competition.example/moves",
                                    token_env=None, timeout=4., interval=0.25, model="untrained")

    def drive(self, replies):
        self.transport = Transport(replies)
        with patch("remote_client.build_opener", return_value=self.transport) as make_opener, \
                patch("remote_client.time.sleep") as self.sleep, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.make_opener = make_opener
            client.connect(self.args)

    def test_poll_retry_submit_replay_and_complete(self):
        work = packet()
        ack = {"status": "accepted", "game_id": "match-1", "request_id": 1}
        self.args.token_env = "TEST_MONOPOLY_TOKEN"
        with patch.dict("os.environ", {"TEST_MONOPOLY_TOKEN": "local-example-token"}):
            self.drive([Response(204, headers={"Retry-After": "3"}), Response(200, work),
                        TimeoutError(), http_error(429, {"Retry-After": "7"}),
                        Response(200, ack), Response(200, work), Response(204),
                        Response(200, {"status": "complete"})])
        requests = [req for req, timeout in self.transport.requests if timeout == self.args.timeout]
        self.assertEqual([req.method for req in requests], ["GET", "GET", "POST", "POST", "POST", "GET", "POST", "GET"])
        posts = [request for request in requests if request.method == "POST"]
        self.assertTrue(all(post is posts[0] for post in posts))
        self.assertEqual(posts[0].full_url, self.args.submit_url)
        self.assertEqual(len(posts[0].get_header("Idempotency-key")), 64)
        self.assertEqual(json.loads(posts[0].data), native.choose(native.model("untrained"), work))
        self.assertTrue(all(req.get_header("Authorization") == "Bearer local-example-token" for req in requests))
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [3, .25, 7])
        # A new client session independently generates the same server deduplication key.
        self.args.token_env = None
        self.drive([Response(200, work), Response(204), Response(200, {"status": "complete"})])
        restarted = self.transport.requests[1][0]
        self.assertEqual(posts[0].get_header("Idempotency-key"), restarted.get_header("Idempotency-key"))
        self.assertEqual(posts[0].data, restarted.data)

    def test_invalid_data_fails_without_post(self):
        for bad in ({}, {"status": "typo"}, {**packet(), "state": {}}, {**packet(), "game_id": False}):
            with self.subTest(bad=bad), self.assertRaises(client.ProtocolError):
                self.drive([Response(200, bad)])
            self.assertEqual(len(self.transport.requests), 1)

    def test_auth_tls_and_redirect_errors_do_not_retry(self):
        for error in (http_error(401), http_error(403), http_error(302),
                      URLError(ssl.SSLCertVerificationError("invalid certificate"))):
            with self.subTest(error=error), self.assertRaises(client.ProtocolError):
                self.drive([error])
            self.assertEqual(len(self.transport.requests), 1)
            self.sleep.assert_not_called()
        installed = self.make_opener.call_args.args[0]
        self.assertIsNone(installed.redirect_request(None, None, 302, "", {}, "https://elsewhere.test/"))

    def test_bearer_origin_and_transport(self):
        self.args.token_env = "TEST_MONOPOLY_TOKEN"
        with patch.dict("os.environ", {"TEST_MONOPOLY_TOKEN": "local-example-token"}):
            for poll, submit in (("https://a.test/p", "https://b.test/a"),
                                 ("https://a.test/p", "https://a.test:444/a"),
                                 ("http://a.test/p", "http://a.test/a")):
                self.args.poll_url, self.args.submit_url = poll, submit
                with self.subTest(poll=poll, submit=submit), self.assertRaises(client.ProtocolError):
                    self.drive([])
                self.assertFalse(self.transport.requests)

    def test_bad_ack_changed_id_and_stale_submission(self):
        work = packet()
        changed = {**work, "player": 1}
        failures = ([Response(200, {"status":"accepted", "game_id":"match-1", "request_id":2})],
                    [http_error(409)], [Response(204), Response(200, changed)])
        for failure in failures:
            with self.assertRaises(client.ProtocolError):
                self.drive([Response(200, work), *failure])
            self.sleep.assert_not_called()

    def test_interrupt_exits(self):
        with patch("remote_client.build_opener") as build, redirect_stderr(io.StringIO()):
            build.return_value.open.side_effect = KeyboardInterrupt()
            with self.assertRaises(SystemExit) as exit_info:
                client.connect(self.args)
        self.assertEqual(exit_info.exception.code, 130)


if __name__ == "__main__":
    unittest.main()
