"""A live, two-seat table backed by the competition referee in src/."""

import ctypes as C
from collections import deque
import threading

from native import (BOARD, CALLBACK, INT, PROTOCOL, Reply, Snapshot,
                    integer, lib, make_packet, snapshot_dict)


EVENT_CALLBACK = C.CFUNCTYPE(INT, C.POINTER(Snapshot), INT, INT, INT, INT)
lib.mono_table.argtypes = [C.c_uint64, INT, CALLBACK, EVENT_CALLBACK, C.POINTER(Snapshot)]
lib.mono_table.restype = INT
lib.mono_table_management.argtypes = [C.POINTER(Snapshot), INT, C.POINTER(INT)]
lib.mono_table_management.restype = None

CARD_TEXT = (
    ("advance to GO", "advance to Illinois Avenue", "advance to St. Charles Place",
     "advance to the nearest utility", "advance to the nearest railroad",
     "advance to the nearest railroad", "collect a $50 dividend", "keep a jail release card",
     "go back three spaces", "go to jail", "pay for building repairs", "pay a $15 speeding fine",
     "advance to Reading Railroad", "advance to Boardwalk", "pay the other player $50",
     "collect $150 from a building loan"),
    ("advance to GO", "collect $200 for a bank error", "pay $50 in doctor’s fees",
     "collect $50 from stock", "keep a jail release card", "go to jail", "collect $100",
     "collect a $20 refund", "collect $10 from the other player", "collect $100 from insurance",
     "pay $100 in hospital fees", "pay $50 in school fees", "collect a $25 consultancy fee",
     "pay for street repairs", "collect a $10 prize", "inherit $100"),
)

ACTION_DEFAULTS = {"value": 0, "square": -1, "give": -1, "take": -1, "cash": 0}


class TableError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def opening_state():
    snapshot = Snapshot()
    snapshot.cash[:] = [1500, 1500]
    snapshot.jail[:] = [-1, -1]
    snapshot.owner[:] = [-1] * 40
    snapshot.houses, snapshot.hotels = 32, 12
    return snapshot_dict(snapshot)


def option(label, value=0, **terms):
    return {"label": label, "action": {"value": value, **terms}}


def legal_moves(snapshot, state, seat, query):
    kind, square, amount = query["kind"], query.get("square", -1), query.get("amount", 0)
    bid, trade = None, None
    cash = state["cash"][seat]
    if kind == "roll":
        options = [option("Roll dice")]
    elif kind == "buy":
        options = [option(f"Buy for ${amount:,}", 1), option("Send to auction")]
    elif kind == "bid":
        options = [option("Pass")]
        if cash >= 10:
            bid = {"min": 10, "max": cash}
    elif kind == "accept":
        options = [option("Accept trade", 1), option("Decline trade")]
    elif kind == "offer":
        options = [option("Skip trade")]
        developed = {BOARD[i]["group"] for i, level in enumerate(state["level"]) if level}
        deeds = [[i for i, owner in enumerate(state["owner"])
                  if owner == who and BOARD[i]["group"] not in developed] for who in (seat, 1-seat)]
        if any(deeds):
            trade = {"give": deeds[0], "take": deeds[1],
                     "min_cash": -state["cash"][1-seat], "max_cash": cash}
    elif kind == "jail":
        options = [option("Attempt doubles")]
        if cash >= 50:
            options.append(option("Pay $50 bail", 1))
        for deck, name in enumerate(("Chance", "Community Chest")):
            if state["cards"][seat] & (1 << deck):
                options.append(option(f"Use {name} release card", 2+deck))
    elif square >= 0:
        options = [option("Keep mortgage"), option(f"Redeem for ${amount:,}", 4, square=square)]
    else:
        # A liquidation pass delegates the choice to the referee only when no assets remain.
        options = [] if kind == "liquidate" else [option("Finish managing" if amount >= 0 else "Pass")]
        flags = (INT * 40)()
        lib.mono_table_management(snapshot, seat, flags)
        for i, mask in enumerate(flags):
            for value, verb in enumerate(("", "Build on", "Sell building on", "Mortgage", "Redeem")):
                if not mask & (1 << value):
                    continue
                if kind == "liquidate" and value not in (2, 3):
                    continue
                if amount < 0 and (value != 1 or (state["level"][i] == 4) != (amount == -2)):
                    continue
                options.append(option(f"{verb} {BOARD[i]['name']}", value, square=i))
        if not options:
            options.append(option("Resolve bankruptcy"))
    return {"options": options, "bid": bid, "trade": trade}


def action_fields(action):
    if not isinstance(action, dict) or action.keys() - ACTION_DEFAULTS.keys():
        raise TableError(400, "Use an action object with value, square, give, take or cash.")
    result = {**ACTION_DEFAULTS, **action}
    try:
        for field in ("square", "give", "take"):
            integer(result[field], -1, 39)
        integer(result["cash"], -100000000, 100000000)
        integer(result["value"], 0, 100000000)
    except ValueError as error:
        raise TableError(400, str(error)) from error
    return result


def validate_action(action, packet):
    legal = packet["legal"]
    if any(action == {**ACTION_DEFAULTS, **o["action"]} for o in legal["options"]):
        return
    if packet["query"]["kind"] == "bid" and action == {**ACTION_DEFAULTS, "value": action["value"]}:
        if action["value"] <= packet["state"]["cash"][packet["player"]]:
            return
    if legal["trade"] and action["value"] in (0, 1) and action["square"] == -1:
        trade = legal["trade"]
        give, take, cash = action["give"], action["take"], action["cash"]
        if ((give == -1 or give in trade["give"]) and (take == -1 or take in trade["take"])
                and (give >= 0 or take >= 0)):
            def fee(i):
                return (BOARD[i]["mortgage"]+9)//10 if i >= 0 and packet["state"]["mortgaged"][i] else 0
            if trade["min_cash"] + fee(give) <= cash <= trade["max_cash"] - fee(take):
                return
    raise TableError(400, "Illegal action. Use this decision’s legal options and limits.")


class TableGame:
    def __init__(self, game_id, seed, turn_cap, names, *, pace=0, human_seats=()):
        self.game_id, self.seed, self.turn_cap = game_id, seed, turn_cap
        self.names = tuple(names)
        self.human_seats = set(human_seats)
        self.pace = pace
        self.cv = threading.Condition()
        self.state = opening_state()
        self.status, self.winner, self.reason = "running", None, None
        self.dice = None
        self.events = deque(maxlen=120)
        self._event_id = self._request_id = 0
        self._pending = self._reply = None
        self._accepted = {}
        self._closed = False
        self._thread = None

    def start(self):
        with self.cv:
            if self._thread is None and not self._closed:
                self._thread = threading.Thread(target=self._run, name=f"table-{self.game_id}", daemon=True)
                self._event("start", None, "Both players received $1,500.")
                self._thread.start()
        return self

    def close(self):
        with self.cv:
            self._closed = True
            self.cv.notify_all()
        if self._thread:
            self._thread.join(timeout=3)

    def view(self):
        with self.cv:
            packet = self._pending
            decision = None
            if packet:
                decision = {key: packet[key] for key in ("player", "request_id", "query")}
                decision.update(packet["legal"])
            return {"status": self.status, "state": self.state, "decision": decision, "dice": self.dice,
                    "events": list(self.events), "winner": self.winner, "reason": self.reason}

    def poll(self, seat):
        with self.cv:
            if self.status in ("complete", "error"):
                return {"status": self.status, "game_id": self.game_id, "winner": self.winner,
                        "reason": self.reason}
            if self._pending and self._pending["player"] == seat:
                return self._pending
            return None

    def submit(self, seat, reply):
        if not isinstance(reply, dict) or reply.get("protocol") != PROTOCOL:
            raise TableError(400, f"Reply protocol must be {PROTOCOL}.")
        request_id = reply.get("request_id")
        if type(request_id) is not int or request_id < 1:
            raise TableError(400, "request_id must be a positive integer.")
        if reply.get("game_id") != self.game_id:
            raise TableError(409, "Reply game_id does not match this game.")
        action = action_fields(reply.get("action"))
        ack = {"status": "accepted", "game_id": self.game_id, "request_id": request_id}
        with self.cv:
            accepted = self._accepted.get(request_id)
            if accepted:
                if accepted != (seat, action):
                    raise TableError(409, "This decision already has a different reply or seat.")
                return ack
            packet = self._pending
            if not packet or packet["request_id"] != request_id or packet["player"] != seat:
                raise TableError(409, "No matching decision is pending for this seat.")
            validate_action(action, packet)
            self._reply = Reply(**action)
            self._accepted[request_id] = (seat, action)
            self._pending = None
            self.cv.notify_all()
        return ack

    def _event(self, kind, seat, message):
        self._event_id += 1
        self.events.append({"id": self._event_id, "turn": self.state["turn"], "seat": seat,
                            "kind": kind, "text": message})

    def _update(self, state):
        previous, self.state = self.state, state
        for seat, history in enumerate(state["behavior"]):
            jail, old_jail = state["jail"][seat], previous["jail"][seat]
            if jail >= 0 and old_jail < 0:
                self._event("jail", seat, f"{self.names[seat]} went to jail.")
            elif old_jail >= 0 and jail < 0:
                self._event("jail", seat, f"{self.names[seat]} left jail.")
            elif jail > old_jail:
                self._event("jail", seat, f"{self.names[seat]} stayed in jail after a failed roll.")
            old = previous["behavior"][seat]
            if old == history:
                continue
            # At 16 entries the referee drops the oldest record before appending.
            overlap = min(len(old), len(history))
            while overlap and old[-overlap:] != history[:overlap]:
                overlap -= 1
            for record in history[overlap:]:
                kind, sq, cost = record["kind"], record["square"], record["amount"]
                verb = {"bought_at_face": "bought", "won_auction": "won"}.get(kind)
                if verb:
                    auction = " at auction" if kind == "won_auction" else ""
                    self._event("purchase", seat,
                                f"{self.names[seat]} {verb} {BOARD[sq]['name']}{auction} for ${cost:,}.")
                elif kind == "proposed_trade":
                    self._event("trade", seat, f"{self.names[seat]} proposed a trade.")
                elif kind in ("accepted_trade", "declined_trade"):
                    verb = "accepted" if kind == "accepted_trade" else "declined"
                    self._event("trade", seat, f"{self.names[seat]} {verb} the trade.")
        for i, owner in enumerate(state["owner"]):
            if owner < 0 or owner != previous["owner"][i]:
                continue
            if state["mortgaged"][i] != previous["mortgaged"][i]:
                verb = "mortgaged" if state["mortgaged"][i] else "redeemed"
                self._event("manage", owner, f"{self.names[owner]} {verb} {BOARD[i]['name']}.")
            old, new = previous["level"][i], state["level"][i]
            if old != new:
                verb = ("built a hotel on" if new == 5 else "built on") if new > old else "sold buildings on"
                self._event("manage", owner, f"{self.names[owner]} {verb} {BOARD[i]['name']}.")

    def _ask(self, packet):
        self._request_id += 1
        packet["request_id"] = self._request_id
        self._pending = packet
        self.cv.notify_all()
        self.cv.wait_for(lambda: self._reply is not None or self._closed)
        if self._closed:
            return None
        reply, self._reply = self._reply, None
        return reply

    def _decide(self, snapshot, seat, request, out):
        packet = make_packet(snapshot.contents, seat, request.contents, self.game_id, 0)
        with self.cv:
            if self._closed:
                return 0
            self._update(packet["state"])
            legal = legal_moves(snapshot, self.state, seat, packet["query"])
            options = legal["options"]
            if len(options) == 1 and not legal["bid"] and not legal["trade"]:
                choice = Reply(**{**ACTION_DEFAULTS, **options[0]["action"]})
            else:
                packet["legal"] = legal
                choice = self._ask(packet)
            if choice is None:
                return 0
            out[0] = choice
            return 1

    def _observe(self, snapshot, kind, seat, a, b):
        with self.cv:
            if self._closed:
                return 0
            self._update(snapshot_dict(snapshot.contents))
            name = self.names[seat]
            if kind == 0:  # Before the native referee draws either die.
                if seat in self.human_seats:
                    query = {"kind": "roll"}
                    packet = {"protocol": PROTOCOL, "game_id": self.game_id, "player": seat,
                              "state": self.state, "query": query,
                              "legal": legal_moves(snapshot, self.state, seat, query)}
                    if self._ask(packet) is None:
                        return 0
                elif self.pace and self.cv.wait_for(lambda: self._closed, timeout=self.pace):
                    return 0
            elif kind == 1:
                self.dice = [a, b]
                self._event("roll", seat, f"{name} rolled {a} + {b} = {a+b}.")
            elif kind == 2:
                self._event("land", seat, f"{name} landed on {BOARD[a]['name']}.")
            elif kind == 3:
                deck = "Community Chest" if a else "Chance"
                self._event("card", seat, f"{name} drew {deck} — {CARD_TEXT[a][b]}.")
            elif kind == 4:
                self._event("rent", seat, f"{name} owes {self.names[a]} ${b:,} in rent.")
            return 1

    def _run(self):
        errors = []

        def guarded(fn):
            def callback(*args):
                try:
                    return fn(*args)
                except Exception as exc:
                    errors.append(exc)
                    return 0
            return callback

        final_state = Snapshot()
        try:
            winner = lib.mono_table(self.seed, self.turn_cap, CALLBACK(guarded(self._decide)),
                                    EVENT_CALLBACK(guarded(self._observe)), C.byref(final_state))
            if errors:
                raise errors[0]
            with self.cv:
                self._pending = None
                if winner == -2:
                    self.status, self.reason = "complete", "closed"
                else:
                    self._update(snapshot_dict(final_state))
                    self.winner = winner if winner >= 0 else None
                    self.status = "complete"
                    self.reason = "bankruptcy" if winner >= 0 else "turn_limit"
                    message = (f"{self.names[winner]} won by bankruptcy." if winner >= 0 else
                               f"The game reached its {self.turn_cap}-turn limit and ended in a draw.")
                    self._event("complete", self.winner, message)
                self.cv.notify_all()
        except Exception as exc:
            with self.cv:
                self.status, self.reason, self._pending = "error", "engine_error", None
                self._event("error", None, f"The referee stopped the game ({exc}).")
                self.cv.notify_all()
