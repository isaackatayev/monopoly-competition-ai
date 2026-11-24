#!/usr/bin/env python3
"""Join a Monopoly Club invite and play using only Python's standard library."""

import argparse
import http.client
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def choose_action(packet):
    """Replace this function with your strategy. The server supplies legal choices."""
    kind = packet["query"]["kind"]
    legal = packet["legal"]
    actions = [option["action"] for option in legal["options"]]
    if kind == "bid" and legal["bid"]:
        ceiling = min(150, legal["bid"]["max"])
        if ceiling >= legal["bid"]["min"]:
            return {"value": ceiling}
    preferred = {"buy": 1, "manage": 1, "jail": 0, "accept": 0, "offer": 0}
    for action in actions:
        if action.get("value", 0) == preferred.get(kind):
            return action
    return actions[0]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(data):
    return json.loads(data.decode("utf-8"))


def exchange(opener, url, *, body=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    delay = 0.5
    while True:
        wait = delay
        try:
            with opener.open(request, timeout=10) as response:
                if response.status == 204:
                    return None, int(response.headers.get("Retry-After", "1"))
                return read_json(response.read()), 0
        except HTTPError as exc:
            with exc:
                if exc.code not in (408, 429, 502, 503, 504):
                    try:
                        detail = read_json(exc.read()).get("error", exc.reason)
                    except (ValueError, AttributeError):
                        detail = exc.reason
                    raise ValueError(f"HTTP {exc.code}: {detail}") from None
                retry = exc.headers.get("Retry-After", "")
                if retry.isdigit():
                    wait = max(wait, int(retry))
        except (URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
            pass
        print(f"Connection interrupted. Retrying in {wait:g}s.", file=sys.stderr)
        time.sleep(wait)
        delay = min(delay * 2, 15)


def session_key(path, origin, game_id, name):
    identity = {"origin": origin, "id": game_id, "name": name}
    key = secrets.token_urlsafe(32)
    if path is None:
        return key
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        saved = read_json(path.read_bytes())
        if any(saved.get(k) != v for k, v in identity.items()):
            raise ValueError("This session file belongs to a different table, host or name.")
        key = saved["join_key"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
            raise ValueError("Invalid join key in session file.")
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({**identity, "join_key": key}, stream)
    return key


def run(invite, name, session, *, policy=None, on_accepted=None):
    url = urlsplit(invite)
    ids = parse_qs(url.query).get("table", [])
    if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
            or url.password is not None or len(ids) != 1 or not re.fullmatch(r"[A-Z0-9]{6}", ids[0])):
        raise ValueError("Copy the full browser invite from Invite players, including its ?table= code.")
    game_id = ids[0]
    origin = f"{url.scheme}://{url.netloc}"
    base = f"/api/tables/{game_id}"
    key = session_key(session, origin, game_id, name)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    seat, _ = exchange(opener, origin + base + "/join",
                       body={"name": name, "kind": "ai", "join_key": key})
    if seat["id"] != game_id or seat["poll_url"] != base + "/poll" or seat["submit_url"] != base + "/submit":
        raise ValueError("The server returned endpoints for a different table.")
    print(f"Joined {game_id} as {name} in seat {seat['seat']}.", flush=True)
    count = 0
    while True:
        packet, wait = exchange(opener, origin + seat["poll_url"], token=seat["token"])
        if packet is None:
            time.sleep(wait)
            continue
        if packet.get("status") == "complete":
            winner = packet["winner"]
            result = "draw" if winner is None else f"seat {winner} won"
            print(f"Game finished ({packet['reason']}): {result}. Submitted {count} decisions.")
            return
        if (packet["protocol"] != "monopoly.duel.v1" or packet["game_id"] != game_id
                or packet["player"] != seat["seat"]):
            raise ValueError("Received a decision for a different game, seat or protocol.")
        reply = {field: packet[field] for field in ("protocol", "game_id", "request_id")}
        reply["action"] = (policy or choose_action)(packet)
        ack, _ = exchange(opener, origin + seat["submit_url"], body=reply, token=seat["token"])
        if (ack.get("status") != "accepted" or ack.get("game_id") != game_id
                or ack.get("request_id") != reply["request_id"]):
            raise ValueError("The server acknowledgement did not match the submitted move.")
        if on_accepted:
            on_accepted(packet, reply)
        count += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("invite", help="browser invite URL, including ?table=...")
    parser.add_argument("--name", default="Example AI", help="display name for the seat")
    parser.add_argument("--session", type=Path, help="private file for rejoining after an interruption")
    args = parser.parse_args()
    try:
        run(args.invite, args.name, args.session)
    except KeyboardInterrupt:
        sys.exit(130)
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(str(exc))
