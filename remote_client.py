"""Outbound participant client for a competition that speaks monopoly.duel.v1."""
import hashlib
import http.client
import json
import math
import os
import ssl
import sys
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import native


class ProtocolError(ValueError):
    """An operator or the competition server must correct this condition."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(url):
    parts = urlsplit(url)
    if (parts.scheme not in ("http", "https") or not parts.hostname or
            parts.username is not None or parts.password is not None or parts.fragment or
            any(ord(c) < 33 or ord(c) == 127 for c in url)):
        raise ProtocolError("Endpoints must be full HTTP(S) URLs without credentials or fragments")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname.lower(), port


def retry_delay(header, fallback):
    if header:
        value = header.strip()
        if value.isascii() and value.isdigit() and len(value) < 12:
            return max(fallback, int(value))
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(fallback, date.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            pass
    return fallback


def exchange(opener, request, timeout, interval):
    """Retry the same request on a lost connection or an explicitly transient HTTP response."""
    delay = interval
    while True:
        retry_after = None
        try:
            with opener.open(request, timeout=timeout) as response:
                data = response.read(65537)
                if len(data) > 65536:
                    raise ProtocolError("Competition response exceeded 64 KiB")
                return response.status, response.headers, data
        except HTTPError as error:
            with error:
                if error.code not in (408, 425, 429, 500, 502, 503, 504):
                    raise ProtocolError(f"Competition {request.method} returned HTTP {error.code}") from None
                retry_after = error.headers.get("Retry-After")
                reason = f"HTTP {error.code}"
        except (URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as error:
            if isinstance(getattr(error, "reason", error), ssl.SSLError):
                raise ProtocolError("Competition TLS connection failed certificate or protocol checks") from None
            reason = "a transport error"
        wait = retry_delay(retry_after, delay)
        print(f"Competition {request.method} received {reason}. Retrying in {wait:g}s.", file=sys.stderr, flush=True)
        time.sleep(wait)
        delay = min(max(30., interval), delay * 2)


def json_object(data):
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("Competition returned invalid UTF-8 JSON") from None
    if not isinstance(obj, dict):
        raise ProtocolError("Competition response must be a JSON object")
    return obj


def decision_id(packet):
    ids = (packet.get("game_id"), packet.get("request_id"))
    for value in ids:
        if type(value) is not int and not (type(value) is str and 0 < len(value) <= 256):
            raise ProtocolError("A decision needs integer or nonempty string game_id and request_id")
    return ids


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def connect(args):
    """Run until the server announces completion or the user interrupts."""
    if not all(math.isfinite(v) and v > 0 for v in (args.timeout, args.interval)):
        raise ProtocolError("Timeout and polling interval must be positive finite seconds")
    poll_origin, submit_origin = origin(args.poll_url), origin(args.submit_url)
    headers = {"Accept": "application/json", "Cache-Control": "no-store"}
    if args.token_env:
        token = os.environ.get(args.token_env)
        if not token:
            raise ProtocolError("The specified bearer-token environment variable is empty or unset")
        if poll_origin != submit_origin:
            raise ProtocolError("Bearer-authenticated endpoints must share the same origin")
        if poll_origin[0] != "https" and poll_origin[1] not in ("localhost", "127.0.0.1", "::1"):
            raise ProtocolError("Bearer authentication requires HTTPS or a loopback HTTP endpoint")
        if any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ProtocolError("Bearer token contains an invalid character")
        headers["Authorization"] = "Bearer " + token

    from series_player import competition_player
    player = competition_player(args.model,getattr(args,"adapt_series",False))
    opener = build_opener(NoRedirect())
    poll = Request(args.poll_url, headers=headers, method="GET")
    previous = None
    submitted = 0
    try:
        while True:
            status, response_headers, data = exchange(opener, poll, args.timeout, args.interval)
            if status == 204:
                time.sleep(retry_delay(response_headers.get("Retry-After"), args.interval))
                continue
            if status != 200:
                raise ProtocolError(f"Unexpected poll status {status}")
            packet = json_object(data)
            if packet.get("status") == "complete":
                print(f"Competition complete. Acknowledged {submitted} decisions.", flush=True)
                return
            ids = decision_id(packet)
            # A retry after a lost acknowledgement must preserve the exact decision and replay key.
            # Reuse the last reply if the server briefly redelivers the same pending request.
            if previous is not None and ids == previous[0]:
                if packet != previous[1]:
                    raise ProtocolError("The competition reused a decision ID with a changed request")
                submit = previous[2]
            else:
                try:
                    reply = player.choose(packet)
                except (KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
                    raise ProtocolError("Invalid public-state decision packet") from error
                key = hashlib.sha256(encode([native.PROTOCOL, *ids])).hexdigest()
                submit = Request(args.submit_url, data=encode(reply), method="POST", headers={
                    **headers, "Content-Type": "application/json", "Idempotency-Key": key})
            status, _, data = exchange(opener, submit, args.timeout, args.interval)
            if status == 200:
                ack = json_object(data)
                if ack.get("status") != "accepted" or decision_id(ack) != ids:
                    raise ProtocolError("Competition acknowledged a different decision or status")
            elif status != 204:
                raise ProtocolError(f"Unexpected submission status {status}")
            if previous is None or ids != previous[0]:
                submitted += 1
            previous = (ids, packet, submit)
    except KeyboardInterrupt:
        print("Disconnected from competition.", file=sys.stderr)
        raise SystemExit(130) from None
