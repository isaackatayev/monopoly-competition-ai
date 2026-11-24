"""Exercise seats, spectators, reconnects and move authorization via real HTTP."""
import argparse
import json
import secrets
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from table_server import TableServer, public_origin


class TableHTTPTests(unittest.TestCase):
    public_url = "https://table.example"

    @classmethod
    def setUpClass(cls):
        cls.host = TableServer(("127.0.0.1", 0), pace=0, public_url=cls.public_url)
        cls.thread = threading.Thread(target=cls.host.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.host.shutdown()
        cls.thread.join()
        cls.host.server_close()

    def request(self, path, body=None, token=None, origin=None):
        conn = HTTPConnection("127.0.0.1", self.host.server_port, timeout=5)
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        try:
            conn.request("GET" if body is None else "POST", path,
                         None if body is None else json.dumps(body), headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, json.loads(data) if data else None
        finally:
            conn.close()

    def test_seats_permissions_and_replay(self):
        status, room = self.request("/api/tables", {"turn_cap":1, "seed":12})
        self.assertEqual(201, status)
        self.assertEqual("waiting", room["status"])
        base = "/api/tables/" + room["id"]
        self.assertNotIn("seed", room)
        self.assertEqual(401, self.request(base + "/poll")[0])
        claim = {"name":"Ada", "kind":"human", "join_key":secrets.token_urlsafe(32)}
        # Two identical joins in flight must share one seat and token.
        with ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(lambda _: self.request(base + "/join", claim), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(200, a[0])
        seat = a[1]
        self.assertEqual(204, self.request(seat["poll_url"], token=seat["token"])[0])
        self.assertEqual(409, self.request(base + "/join", {**claim, "name":"Another"})[0])
        rival = self.request(base + "/join", {"name":"Rival", "join_key":secrets.token_urlsafe(32)})[1]
        self.assertEqual(1, rival["seat"])
        self.assertEqual(409, self.request(base + "/join", {**claim, "join_key":secrets.token_urlsafe(32)})[0])
        public = self.request(base)[1]
        self.assertNotIn(seat["token"], json.dumps(public))
        self.assertNotIn(claim["join_key"], json.dumps(public))
        until = time.monotonic() + 5
        replayed = False
        while time.monotonic() < until:
            for person, other in ((seat, rival), (rival, seat)):
                status, packet = self.request(person["poll_url"], token=person["token"])
                if status == 204:
                    continue
                self.assertEqual(200, status)
                if packet.get("status") == "complete":
                    self.assertTrue(replayed)
                    self.assertEqual("turn_limit", packet["reason"])
                    self.assertEqual(12, self.request(base)[1]["seed"])
                    return
                reply = {k:packet[k] for k in ("protocol", "game_id", "request_id")}
                reply["action"] = packet["legal"]["options"][0]["action"]
                submit = person["submit_url"]
                self.assertEqual(409, self.request(submit, reply, other["token"])[0])
                self.assertEqual(400, self.request(submit, {**reply, "action":{"value":999999}}, person["token"])[0])
                ack = self.request(submit, reply, person["token"])
                self.assertEqual(200, ack[0])
                self.assertEqual(ack, self.request(submit, reply, person["token"]))
                replayed = True
            time.sleep(.002)
        self.fail("Game did not reach the configured turn limit")

    def test_invalid_create(self):
        for bad in ({"seats":["open"]}, {"seats":["open","bot"]}, {"seats":["bot","bot"]}, {"seed":True}, {"turn_cap":0}):
            self.assertEqual(400, self.request("/api/tables", bad)[0])
        for path in ("/competition", "/models/adaptive.json", "/models/neural.bin", "/src/policy.cpp", "/.git/config"):
            self.assertEqual(404, self.request(path)[0])
        self.assertEqual(403, self.request("/api/tables", {}, origin="https://elsewhere.example")[0])
        status, board = self.request("/api/board")
        self.assertEqual((status, len(board), board[39]["name"]), (200, 40, "Boardwalk"))

    def test_tunnel_and_local_origins(self):
        status, info = self.request("/api/host")
        self.assertEqual((status, info["public_url"], info["lan_enabled"]), (200, self.public_url, False))
        status, room = self.request("/api/tables", {}, origin=self.public_url)
        self.assertEqual(201, status)
        claim = {"name": "Ada", "kind": "human", "join_key": secrets.token_urlsafe(32)}
        status, seat = self.request(f"/api/tables/{room['id']}/join", claim, origin=self.public_url)
        self.assertEqual((status, seat["seat"]), (200, 0))
        local_origin = f"http://127.0.0.1:{self.host.server_port}"
        self.assertEqual(201, self.request("/api/tables", {}, origin=local_origin)[0])
        self.assertEqual(403, self.request("/api/tables", {}, origin=self.public_url + ".elsewhere.example")[0])

    def test_public_origin_option(self):
        self.assertEqual(public_origin("https://TABLE.example:443/"), self.public_url)
        self.assertEqual(public_origin("https://table.example:8443"), self.public_url + ":8443")
        for invalid in ("http://table.example", "https://table.example/path", "https://user@table.example",
                        "https://table.example/?table=ABC234"):
            with self.subTest(invalid=invalid), self.assertRaises(argparse.ArgumentTypeError):
                public_origin(invalid)


if __name__ == "__main__":
    unittest.main()
