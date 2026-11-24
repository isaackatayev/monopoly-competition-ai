"""Host joinable, in-memory Monopoly tables locally, on a LAN or through an HTTPS tunnel."""
import argparse
import ipaddress
import json
import platform
import re
import secrets
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
FILES = {
    "/": ("web_table/index.html", "text/html"),
    "/style.css": ("web_table/style.css", "text/css"),
    "/board.js": ("web_table/board.js", "text/javascript"),
    "/app.js": ("web_table/app.js", "text/javascript"),
    "/protocol": ("TABLE_PROTOCOL.md", "text/plain"),
    "/example-agent.py": ("example_table_agent.py", "text/plain"),
}


class APIError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def number(value, lo, hi, label):
    if type(value) is not int or not lo <= value <= hi:
        raise APIError(400, f"{label} must be an integer from {lo} to {hi}.")
    return value


class Room:
    def __init__(self, table_id, body, pace):
        self.id = table_id
        self.pace = pace
        self.lock = threading.RLock()
        seats = body.get("seats", ["open", "open"])
        if seats != ["open", "open"]:
            raise APIError(400, "This host accepts two open guest seats.")
        self.turn_cap = number(body.get("turn_cap", 500), 1, 1000, "turn_cap")
        self.seed = number(body.get("seed", secrets.randbelow(2**31)), 0, 2**31 - 1, "seed")
        self.players = [dict(seat=i, kind="open", name=None) for i in range(2)]
        self.claims = {}
        self.tokens = {}
        self.game = None

    def start_if_ready(self):
        from table_game import TableGame
        if self.game or any(p["kind"] == "open" for p in self.players):
            return
        game = TableGame(self.id, self.seed, self.turn_cap, [p["name"] for p in self.players],
                         human_seats=[p["seat"] for p in self.players if p["kind"] == "human"],
                         pace=self.pace)
        game.start()
        self.game = game

    def join(self, body):
        kind, key, name = body.get("kind", "ai"), body.get("join_key"), body.get("name")
        if kind not in ("ai", "human"):
            raise APIError(400, "kind must be ai or human.")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
            raise APIError(400, "Supply a random URL-safe join_key of 32 to 128 characters.")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32 or not name.strip().isprintable():
            raise APIError(400, "Choose a printable player name of 1 to 32 characters.")
        name = name.strip()
        with self.lock:
            if key in self.claims:
                token = self.claims[key]
                seat = self.tokens[token]
                if (self.players[seat]["name"], self.players[seat]["kind"]) != (name, kind):
                    raise APIError(409, "That join_key already belongs to another player.")
            else:
                seat = next((p["seat"] for p in self.players if p["kind"] == "open"), None)
                if seat is None:
                    raise APIError(409, "Both seats are taken. You can still watch this table.")
                token = secrets.token_urlsafe(32)
                self.players[seat].update(name=name, kind=kind)
                try:
                    self.start_if_ready()
                except Exception:
                    self.players[seat].update(name=None, kind="open")
                    raise
                self.claims[key] = token
                self.tokens[token] = seat
            base = f"/api/tables/{self.id}"
            return dict(id=self.id, seat=seat, token=token, poll_url=base + "/poll", submit_url=base + "/submit")

    def authorize(self, header):
        with self.lock:
            if not header.startswith("Bearer ") or header[7:] not in self.tokens:
                raise APIError(401, "A valid bearer token for this table is required.")
            return self.tokens[header[7:]]

    def view(self):
        with self.lock:
            state = self.game.view() if self.game else dict(status="waiting", state=None, decision=None,
                                                             events=[], dice=None, winner=None, reason=None)
            result = dict(state, id=self.id, players=[p.copy() for p in self.players], turn_cap=self.turn_cap)
            if result["status"] == "complete":
                result["seed"] = self.seed
            return result


class TableServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, pace=.65, public_url=None):
        from native import board_info
        self.rooms = {}
        self.lock = threading.Lock()
        self.board = board_info()
        self.pace = pace
        self.public_url = public_url
        super().__init__(address, Handler)
        self.lan_urls = [f"http://{ip}:{self.server_port}" for ip in lan_addresses()] if address[0] == "0.0.0.0" else []

    def create(self, body):
        with self.lock:
            if len(self.rooms) >= 128:
                retired = next((k for k, r in self.rooms.items() if r.game and
                                r.game.view()["status"] in ("complete", "error")), None)
                if retired is None:
                    raise APIError(503, "All tables are in use.")
                del self.rooms[retired]
            while True:
                table_id = "".join(secrets.choice(ALPHABET) for _ in range(6))
                if table_id not in self.rooms:
                    break
            room = Room(table_id, body, self.pace)
            self.rooms[table_id] = room
            return room.view()

    def server_close(self):
        super().server_close()
        for room in list(self.rooms.values()):
            if room.game:
                room.game.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "MonopolyClub/1.0"

    def send_data(self, status, data=None, content_type="application/json"):
        if data is None:
            data = b""
        elif not isinstance(data, bytes):
            data = json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if status == 204:
            self.send_header("Retry-After", "1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def read_object(self):
        origin = self.headers.get("Origin")
        if origin and origin not in ("http://" + self.headers.get("Host", ""), self.server.public_url):
            raise APIError(403, "Open this table from the host that serves it.")
        if self.headers.get_content_type() != "application/json":
            raise APIError(415, "Send Content-Type: application/json.")
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise APIError(400, "Supply a JSON object with Content-Length.") from None
        if not 1 <= size <= 65536:
            raise APIError(413 if size > 65536 else 400, "Request must contain 1 to 65536 bytes.")
        try:
            obj = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            raise APIError(400, "Expected a UTF-8 JSON object.") from None
        if not isinstance(obj, dict):
            raise APIError(400, "Expected a JSON object.")
        return obj

    def serve(self, method):
        path = urlsplit(self.path).path
        if method == "GET":
            if path in FILES:
                file, mime = FILES[path]
                return self.send_data(200, (ROOT / file).read_bytes(), mime)
            if path == "/api/board":
                return self.send_data(200, self.server.board)
            if path == "/api/host":
                return self.send_data(200, dict(public_url=self.server.public_url, lan_urls=self.server.lan_urls,
                                                lan_enabled=self.server.server_address[0] == "0.0.0.0"))
            if path == "/favicon.ico":
                return self.send_data(204)
        if method == "POST" and path == "/api/tables":
            return self.send_data(201, self.server.create(self.read_object()))
        match = re.fullmatch(r"/api/tables/([A-Za-z0-9]{6})(?:/(join|poll|submit))?", path)
        if not match:
            raise APIError(404, "Unknown endpoint.")
        with self.server.lock:
            room = self.server.rooms.get(match[1].upper())
        if room is None:
            raise APIError(404, "Table not found on this host.")
        action = match[2]
        if method == "GET" and not action:
            return self.send_data(200, room.view())
        if method == "POST" and action == "join":
            return self.send_data(200, room.join(self.read_object()))
        if (method, action) not in (("GET", "poll"), ("POST", "submit")):
            raise APIError(404, "Unknown endpoint.")
        seat = room.authorize(self.headers.get("Authorization", ""))
        if action == "poll":
            packet = room.game.poll(seat) if room.game else None
            if packet and packet.get("status") == "error":
                raise APIError(500, "The game stopped with an engine error.")
            return self.send_data(200 if packet else 204, packet)
        reply = self.read_object()
        if room.game is None:
            raise APIError(409, "The table is waiting for its other player.")
        return self.send_data(200, room.game.submit(seat, reply))

    def dispatch(self, method):
        from table_game import TableError
        try:
            self.serve(method)
        except (APIError, TableError) as exc:
            self.send_data(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            self.send_data(500, {"error": "The host could not complete the request."})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def log_message(self, format, *args):
        if getattr(self, "command", None) == "POST" or (len(args) > 1 and str(args[1]).startswith(("4", "5"))):
            super().log_message(format, *args)


def lan_addresses():
    addresses = []
    # Prefer the physical LAN interface over VPN or virtualization addresses.
    if platform.system() == "Darwin":
        for interface in ("en0", "en1", "en2", "en3"):
            try:
                output = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface], capture_output=True,
                                        text=True, timeout=1).stdout.strip()
                if output:
                    addresses.append(output)
            except (OSError, subprocess.TimeoutExpired):
                pass
    try:
        addresses.extend(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    if platform.system() == "Linux":
        try:
            addresses.extend(subprocess.run(["hostname", "-I"], capture_output=True, text=True,
                                            timeout=1).stdout.split())
        except (OSError, subprocess.TimeoutExpired):
            pass
    valid = []
    for addr in dict.fromkeys(addresses):
        try:
            ip = ipaddress.ip_address(addr)
            if ip.version == 4 and not ip.is_loopback and not ip.is_unspecified and not ip.is_link_local:
                valid.append(addr)
        except ValueError:
            pass
    return valid


def public_origin(value):
    message = "Use the public HTTPS origin, for example https://your-domain.ngrok.app."
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise argparse.ArgumentTypeError(message) from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", parsed.netloc)):
        raise argparse.ArgumentTypeError(message)
    hostname = parsed.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"https://{hostname}" + (f":{port}" if port not in (None, 443) else "")


def configure(parser):
    parser.add_argument("--lobby-code", help="Reopen a guest lobby with this six-character code on startup.")
    parser.add_argument("--lan", action="store_true", help="Allow players on the same network to join.")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765).")
    parser.add_argument("--public-url", type=public_origin, help="Public HTTPS origin to use for tunnel invites.")


def host(args):
    if args.lobby_code and not re.fullmatch(r"[A-Z2-9]{6}", args.lobby_code):
        raise ValueError("Choose six uppercase letters or digits 2–9 for the lobby code.")
    if not 0 <= args.port <= 65535:
        raise ValueError("Choose a port from 0 to 65535.")
    with TableServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), public_url=args.public_url) as server:
        if args.lobby_code:
            server.rooms[args.lobby_code] = Room(args.lobby_code, {}, server.pace)
        print(f"Monopoly Club is open at http://localhost:{server.server_port}", flush=True)
        if server.public_url:
            print(f"Players can join through the tunnel at {server.public_url}", flush=True)
        for url in server.lan_urls:
            print(f"Same-network players can join at {url}", flush=True)
        if args.lan and not server.lan_urls:
            print(f"LAN enabled. Use this computer's local IPv4 address with port {server.server_port}.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    host(parser.parse_args())
