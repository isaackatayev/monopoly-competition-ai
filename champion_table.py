"""Run the private table Champion and record acquisition and trade decisions locally."""
import argparse
import ctypes as C
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from example_table_agent import run
from live_player import LivePlayer
from native import BOARD, model, lib
from table_game import TableError, action_fields, validate_action


class Metric(C.Structure):
    _fields_ = [("name", C.c_char_p), ("value", C.c_double)]


class BidTrace(C.Structure):
    _fields_ = [("reason", C.c_char_p), ("metrics", Metric * 12), ("count", C.c_int)]


class Champion:
    def __init__(self, log):
        self.player = LivePlayer(model("table-champion"))
        self.log = log
        lib.mono_set_bid_trace.argtypes = [C.POINTER(BidTrace)]
        lib.mono_set_bid_trace.restype = None

    def record(self, packet, phase, **fields):
        record = dict(time=datetime.now(timezone.utc).isoformat(), game_id=packet["game_id"],
                      request_id=packet["request_id"], turn=packet["state"]["turn"],
                      seat=packet["player"], phase=phase, **fields)
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps(record, ensure_ascii=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def choose(self, packet):
        query = packet["query"]
        bid = query["kind"] == "bid"
        trace = BidTrace()
        if bid:
            lib.mono_set_bid_trace(C.byref(trace))
        try:
            chosen = self.player.choose(packet)["action"]
        finally:
            if bid:
                lib.mono_set_bid_trace(None)
        action = chosen
        fallback = None
        try:
            validate_action(action_fields(action), packet)
        except TableError as exc:
            fallback = str(exc)
            action = packet["legal"]["options"][0]["action"]
        if bid:
            deed = BOARD[query["square"]]
            self.record(packet, "selected", property=deed["name"], query=query, face_price=deed["price"],
                        cash_by_seat=packet["state"]["cash"], model_bid=chosen["value"],
                        submission_ceiling=action.get("value", 0), validation_fallback=fallback,
                        reason=trace.reason.decode() if trace.reason else "No native trace returned.",
                        metrics={m.name.decode():m.value for m in trace.metrics[:trace.count]},
                        decision_packet=packet)
        elif query["kind"] in ("buy", "offer", "accept"):
            self.record(packet, "selected", query=query, model_action=chosen, submission=action,
                        validation_fallback=fallback, decision_packet=packet)
        return action

    def accepted(self, packet, reply):
        if packet["query"]["kind"] == "bid":
            self.record(packet, "accepted", property=BOARD[packet["query"]["square"]]["name"],
                        accepted_ceiling=reply["action"].get("value", 0))
        elif packet["query"]["kind"] in ("buy", "offer", "accept"):
            self.record(packet, "accepted", query=packet["query"], accepted_action=reply["action"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("invite")
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    champion = Champion(args.log)
    run(args.invite, "Club Champion", args.session, policy=champion.choose, on_accepted=champion.accepted)
