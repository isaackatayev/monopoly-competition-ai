"""Run the real competition CLI against a single local, head-to-head game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena import example, local_game
from live_player import LivePlayer
from native import MODEL, choose, model


class PlayerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, obj=None):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, path):
        if self.path == path and self.headers.get("Authorization") == "Bearer fixture-token":
            return True
        self.send(403)
        return False

    def do_GET(self):
        if not self.authorized("/poll"):
            return
        s = self.server
        if not s.sent_idle:
            s.sent_idle = True
            self.send(204)
        elif s.pending is None:
            self.send(200, {"status": "complete"})
            s.finished = True
        else:
            self.send(200, s.pending)

    def do_POST(self):
        if not self.authorized("/submit"):
            return
        s = self.server
        data = self.rfile.read(int(self.headers["Content-Length"]))
        answer = json.loads(data)
        ids = [s.pending[k] for k in ("protocol", "game_id", "request_id")]
        key = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
        if self.headers.get("Idempotency-Key") != key or answer != s.expected_reply:
            self.send(409)
            return
        if key in s.committed:
            assert answer == s.committed[key]
            s.replayed += 1
        else:
            s.committed[key] = answer
            if len(s.committed) == 1:
                # First move was applied, but its acknowledgement was lost.
                self.send(503)
                return
        self.send(200, {"status": "accepted", "game_id": ids[1], "request_id": ids[2]})
        s.answer = answer


def run(name):
    strategy, entrant = model("builder"), model(name)
    seed, cap, seat, game_id = 303, 2000, 1, "http-integration"
    direct = local_game(strategy, lambda p: choose(entrant, p), seed, cap, seat, game_id)
    assert direct["winner"] in (0, 1), "Fixture seed must reach bankruptcy"
    with HTTPServer(("127.0.0.1", 0), PlayerHandler) as server:
        server.timeout = .1
        server.sent_idle = server.finished = False
        server.committed, server.replayed = {}, 0
        url = "http://127.0.0.1:%d" % server.server_port
        args = [str(ROOT / "monopoly"), "connect", "--poll-url", url + "/poll",
                "--submit-url", url + "/submit", "--model", name, "--interval", ".005",
                "--timeout", "2", "--token-env", "MONOPOLY_FIXTURE_TOKEN"]
        env = {**os.environ, "MONOPOLY_FIXTURE_TOKEN": "fixture-token",
               "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}
        with subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as player:
            def pump_until(done):
                deadline = time.monotonic() + 8
                while not done():
                    if player.poll() is not None:
                        out, err = player.communicate()
                        raise AssertionError(f"Client exited early ({player.returncode}): {out}{err}")
                    if time.monotonic() > deadline:
                        raise TimeoutError("No progress on loopback player connection")
                    server.handle_request()

            reference = LivePlayer(entrant)

            def ask(packet):
                server.pending = packet
                server.expected_reply = reference.choose(packet)
                server.answer = None
                pump_until(lambda: server.answer is not None)
                return server.answer

            try:
                package_requests=0
                if len(entrant)>2 and entrant[2]:
                    for scenario in ("bundle-offer","bundle-trade"):
                        packet=example(scenario)
                        packet["game_id"]="http-"+scenario
                        response=ask(packet)["action"]
                        assert "give_deeds" in response and "give_cards" in response
                        if scenario=="bundle-trade":
                            assert response["give_deeds"]==[16,18] and response["give_cards"]==["chance"]
                        package_requests+=1
                result = local_game(strategy, ask, seed, cap, seat, game_id)
                server.pending = None
                pump_until(lambda: server.finished)
                output, errors = player.communicate(timeout=4)
                assert player.returncode == 0, output + errors
            finally:
                if player.poll() is None:
                    player.kill()
                    player.communicate()
    assert server.sent_idle and server.replayed == 1
    assert len(server.committed) == result["rival_decisions"]+package_requests
    assert result == direct, (result, direct)
    print(f"HTTP player matched all {result['rival_decisions']} local decisions over "
          f"{result['turns']} turns. Seat {result['winner']} won by bankruptcy. "
          f"{package_requests} package requests completed. The first submission was retried and applied once.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="champion" if MODEL.is_file() else "untrained")
    run(parser.parse_args().model)
