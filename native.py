"""C interface to the simulator. Only public game state crosses the bot boundary."""
import ctypes as C
import json
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEGACY_MODEL = ROOT / "models" / "champion.json"
MODEL = ROOT / "models" / "neural.json"
ADAPTIVE_MODEL = ROOT / "models" / "adaptive.json"
EVENT_KINDS = ("declined_trade", "accepted_trade", "proposed_trade", "bought_at_face", "declined_face", "won_auction",
               "declined_bundle", "accepted_bundle", "proposed_bundle")
RESILIENCE_BOUNDS = ((0,1),(0,1),(0,1),(.5,2),(.5,2),(-150,150),(0,1),(0,1))
PRESSURE_BOUNDS = ((1,2.2),(.5,1.8),(0,240),(0,2),(0,2),(0,1),(0,1),
                   (.5,1.75),(.5,1.75),(.7,1.8),(0,1),(0,1))
ADAPTIVE_FORMATS = tuple("monopoly.adaptive.v"+str(i) for i in range(1,5))
EVENT_FIELDS = ("kind", "square", "amount", "turn", "give", "take", "generosity")
PROTOCOL = "monopoly.duel.v1"
STYLES = {"collector": 1, "builder": 2, "cautious": 3, "untrained": 0, "blocker": 4}
KINDS = ("buy", "bid", "manage", "liquidate", "jail", "offer", "accept")
WEIGHTS = C.c_double * 12
INT = C.c_int
try:
    lib = C.CDLL(str(ROOT / "build" / "libmonopoly.so"))
except OSError as e:
    raise SystemExit(f"Build the native simulator with 'make' first. {e}") from e


class Snapshot(C.Structure):
    _fields_ = [(x, INT * 2) for x in ("cash", "position", "jail", "cards")] + [
        (x, INT * 40) for x in ("owner", "level", "mortgaged")
    ] + [(x, INT) for x in ("houses", "hotels", "turn", "active")] + [
        ("rejected", (INT * 4) * 2), ("history_size", INT * 2), ("history", ((INT*7)*16)*2)]


class Request(C.Structure):
    _fields_ = [(x, INT) for x in ("kind", "square", "amount", "give", "take", "cash")]


class Reply(C.Structure):
    _fields_ = [(x, INT) for x in ("value", "square", "give", "take", "cash")]


class Specialist(C.Structure):
    _fields_ = [("weights", C.c_double*20)]


class Resilience(C.Structure):
    _fields_ = [("weights", C.c_double*8)]


class Robust(C.Structure):
    _fields_ = [("weights", C.c_double*12)]

class Pressure(C.Structure):
    _fields_ = [("weights", C.c_double*len(PRESSURE_BOUNDS))]

class Config(C.Structure):
    _fields_ = [("style", INT), ("weights", C.POINTER(C.c_double)), ("net", C.c_void_p),
                ("response", C.c_void_p), ("opponent", C.c_void_p), ("specialist", C.POINTER(Specialist)),
                ("resilience", C.POINTER(Resilience)), ("robust", C.POINTER(Robust)), ("deal_net", C.c_void_p),
                ("pressure", C.POINTER(Pressure))]


class Summary(C.Structure):
    _fields_ = [(x, C.c_int64) for x in ("wins", "losses", "caps", "turns", "first_wins", "first_games",
                    "offers_sent", "outgoing_accepted", "offers_received", "incoming_accepted")]

    def as_dict(self):
        return {f: getattr(self, f) for f, _ in self._fields_}


ptr = C.POINTER
CALLBACK = C.CFUNCTYPE(INT, ptr(Snapshot), INT, ptr(Request), ptr(Reply))
lib.mono_param.argtypes = [INT]
lib.mono_param.restype = C.c_char_p
lib.mono_default.argtypes = [INT, ptr(C.c_double)]
lib.mono_batch.argtypes = [Config, Config, C.c_uint64,
                           INT, INT, INT, ptr(Summary)]
lib.mono_external.argtypes = [Config, C.c_uint64, INT, INT, CALLBACK, ptr(INT)]
lib.mono_space.argtypes = [INT, ptr(INT)]
lib.mono_space.restype = C.c_char_p
lib.mono_decide.argtypes = [Config, ptr(Snapshot), INT, ptr(Request), ptr(Reply)]
lib.mono_load_network.argtypes = [C.c_char_p]
lib.mono_load_network.restype = C.c_void_p
lib.mono_load_response.argtypes = [C.c_char_p]
lib.mono_load_response.restype = C.c_void_p
lib.mono_free_response.argtypes = [C.c_void_p]
lib.mono_free_response.restype = None
lib.mono_free_network.argtypes = [C.c_void_p]
lib.mono_free_network.restype = None
lib.mono_load_opponent.argtypes = [C.c_char_p]
lib.mono_load_opponent.restype = C.c_void_p
lib.mono_free_opponent.argtypes = [C.c_void_p]
lib.mono_free_opponent.restype = None
lib.mono_load_deal.argtypes = [C.c_char_p]
lib.mono_load_deal.restype = C.c_void_p
lib.mono_free_deal.argtypes = [C.c_void_p]
lib.mono_free_deal.restype = None
lib.mono_scout.argtypes = [Config, ptr(Snapshot), INT, ptr(C.c_float)]
lib.mono_scout.restype = None
lib.mono_generosity.argtypes = [ptr(Snapshot), INT, INT, INT, INT]
lib.mono_generosity.restype = INT
lib.mono_assess.argtypes = [Config, ptr(Snapshot), INT, ptr(Request), ptr(C.c_float), ptr(INT)]
for _function in (lib.mono_batch, lib.mono_default, lib.mono_decide):
    _function.restype = None
NAMES = [lib.mono_param(i).decode() for i in range(12)]


def preset(style):
    w = WEIGHTS()
    lib.mono_default(style, w)
    return style, list(w)


def model_data(weights):
    return {"format": "monopoly.policy.v1", "weights": dict(zip(NAMES, weights))}


class Network:
    def __init__(self, path, checksum, kind="network"):
        self.handle = None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != checksum:
            raise ValueError(f"Network weights failed checksum verification: {path}")
        loader = getattr(lib, "mono_load_" + kind)
        self.free = getattr(lib, "mono_free_" + kind)
        self.handle = loader(os.fsencode(path))
        if not self.handle:
            raise ValueError(f"Invalid neural network in {path}")
        self.sha256 = digest

    def __del__(self):
        if self.handle:
            self.free(self.handle)


def config(strategy):
    style, w = strategy[:2]
    return Config(style, WEIGHTS(*w), strategy[2].handle if style >= 6 else None,
                  strategy[3].handle if style >= 6 else None, strategy[4].handle if style >= 7 else None,
                  C.pointer(strategy[5]) if style >= 7 else None, C.pointer(strategy[6]) if style >= 8 else None,
                  C.pointer(strategy[7]) if style >= 9 else None,
                  strategy[8].handle if style >= 9 and strategy[8] is not None else None,
                  C.pointer(strategy[9]) if style >= 10 else None)


def model(name=None):
    if name == "table-champion":
        original = model(ROOT / "models" / "table-champion.json")
        if original[0] != 11:
            raise ValueError("The table strategy requires the observed-market v4 checkpoint.")
        return (12, *original[1:])
    if name in STYLES:
        return preset(STYLES[name])
    if name in ("legacy", "fast"):
        path = LEGACY_MODEL
    elif name == "pressure":
        path = ROOT/"models"/"pressure-player.json"
    elif name in ("adaptive-v1", "adaptive-v2", "adaptive-v3", "adaptive-v4"):
        path = ROOT / "models" / (name + ".json")
    elif name in ("frozen", "neural-v1"):
        path = MODEL
    else:
        path = ADAPTIVE_MODEL if name in (None, "champion", "neural", "adaptive") else Path(name)
    raw = path.read_bytes()
    data = json.loads(raw)
    if data["format"] in ADAPTIVE_FORMATS:
        return _load_adaptive(path, raw, data)
    return _load_base(path, data, name)


def _load_adaptive(path, raw, data):
    for attempt in range(2):
        files = {key: path.parent/data[key] for key in ("base","specialist","opponent")}
        base_raw = files["base"].read_bytes()
        if hashlib.sha256(base_raw).hexdigest() == data["base_sha256"]:
            break
        # During the one-time migration, an old adaptive entry may still
        # reference the root neural.json when that entry is atomically replaced.
        if attempt == 0:
            current = path.read_bytes()
            if current != raw:
                data = json.loads(current)
                if data["format"] not in ADAPTIVE_FORMATS:
                    raise ValueError(f"Invalid replacement adaptive model in {path}")
                continue
        raise ValueError(f"base checksum failed in {path}")
    base_data = json.loads(base_raw)
    if base_data["format"] != "monopoly.neural.v1":
        raise ValueError("Adaptive model requires a neural v1 base")
    base = _load_base(files["base"], base_data)
    params_raw = files["specialist"].read_bytes()
    if hashlib.sha256(params_raw).hexdigest() != data["specialist_sha256"]:
        raise ValueError(f"specialist checksum failed in {path}")
    params = json.loads(params_raw)["weights"]
    if len(params) != 20 or any(type(v) not in (float, int) or not 0 <= v <= 1 for v in params):
        raise ValueError("Invalid specialist weights")
    specialist = Specialist((C.c_double*20)(*params))
    specialist.sha256 = data["specialist_sha256"]
    parts = (*base[1:], Network(files["opponent"], data["opponent_sha256"], "opponent"), specialist)
    if data["format"] == "monopoly.adaptive.v1":
        return (7, *parts)
    raw = (path.parent / data["resilience"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != data["resilience_sha256"]:
        raise ValueError(f"resilience checksum failed in {path}")
    config_data = json.loads(raw)
    values = config_data["weights"]
    if (config_data.get("format") != "monopoly.resilience.v1" or len(values) != 8 or
            any(type(x) not in (float,int) or not lo <= x <= hi
                for x, (lo,hi) in zip(values,RESILIENCE_BOUNDS))):
        raise ValueError("Invalid bidding model")
    bidding = Resilience((C.c_double*8)(*values))
    bidding.sha256 = data["resilience_sha256"]
    resilience = bidding
    if data["format"] in ("monopoly.adaptive.v3","monopoly.adaptive.v4"):
        raw = (path.parent/data["robust"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != data["robust_sha256"]:
            raise ValueError(f"Robust bidding model checksum failed in {path}")
        robust_data = json.loads(raw)
        values = robust_data["weights"]
        if (robust_data.get("format") != "monopoly.robust.v1" or robust_data.get("evidence_mode") != 4
                or len(values) != 12 or any(type(x) not in (int,float) or not 0 <= x <= 1 for x in values)):
            raise ValueError("Invalid bidding model weights")
        bidding = Robust((C.c_double*12)(*values))
        bidding.sha256 = data["robust_sha256"]
        if ("deal_value" in data) != ("deal_value_sha256" in data):
            raise ValueError("Incomplete deal network manifest")
        deal = Network(path.parent/data["deal_value"],data["deal_value_sha256"],"deal") if "deal_value" in data else None
        if data["format"] == "monopoly.adaptive.v3":
            return (9, *parts, resilience, bidding, deal)
        raw = (path.parent/data["pressure"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != data["pressure_sha256"]:
            raise ValueError("Pressure checkpoint failed checksum verification")
        profile = json.loads(raw)
        values = profile["weights"]
        if (profile.get("format") != "monopoly.pressure.v1" or len(values) != len(PRESSURE_BOUNDS)
                or any(type(v) not in (float,int) or not lo <= v <= hi for v,(lo,hi) in zip(values,PRESSURE_BOUNDS))
                or data.get("acquisition_mode") not in ("pressure","observed") or deal is None):
            raise ValueError("Invalid pressure profile")
        strategy = 10 if data["acquisition_mode"] == "pressure" else 11
        pressure = Pressure((C.c_double*len(values))(*values))
        pressure.sha256 = data["pressure_sha256"]
        return (strategy,*parts,resilience,bidding,deal,pressure)
    return (8, *parts, resilience)


def _load_base(path, data, name=None):
    if data["format"] == "monopoly.neural.v1":
        values = data["prior_weights"]
        style = 6
    elif data["format"] == "monopoly.policy.v1":
        values = data["weights"]
        style = 5 if data.get("trade_search") == "paired-rollouts-v1" and name != "fast" else 0
    else:
        raise ValueError(f"Incompatible model in {path}")
    if set(values) != set(NAMES):
        raise ValueError(f"Incompatible prior in {path}")
    w = [float(values[k]) for k in NAMES]
    if any(not 0 <= v <= 1 for v in w):
        raise ValueError(f"Invalid weight in {path}")
    if style == 6:
        return (style, w, Network(path.parent / data["value_network"], data["sha256"]),
                Network(path.parent / data["response_network"], data["response_sha256"], kind="response"))
    return style, w

def batch(a, b, seed, games, cap, paired=False):
    out = Summary()
    lib.mono_batch(config(a), config(b), seed, games, cap, paired, C.byref(out))
    return out.as_dict()


def board_info():
    states = []
    for i in range(40):
        fields = (INT * 10)()
        name = lib.mono_space(i, fields).decode()
        states.append(dict(square=i, name=name, group=fields[0], price=fields[1],
                           mortgage=fields[2], building_cost=fields[3], rents=list(fields[4:])))
    return states


def snapshot_dict(v):
    result = {f: list(getattr(v, f)) if issubclass(t, C.Array) else getattr(v, f)
              for f, t in v._fields_ if f not in ("rejected", "history_size", "history")}
    result["behavior"] = [[], []]
    for seat in range(2):
        for row in v.history[seat][:v.history_size[seat]]:
            event = dict(zip(EVENT_FIELDS, row))
            event["kind"] = EVENT_KINDS[event["kind"]]
            result["behavior"][seat].append(event)
    if any(r[3] for r in v.rejected):
        result["last_rejected"] = [dict(zip(("give","take","cash","turn"), r)) if r[3] else None
                                   for r in v.rejected]
    return result


def make_packet(v, who, q, game_id, request_id):
    query = {f: getattr(q, f) for f, _ in q._fields_}
    query["kind"] = KINDS[q.kind]
    return dict(protocol=PROTOCOL, game_id=game_id, request_id=request_id, player=who,
                state=snapshot_dict(v), query=query)


def integer(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"Expected integer between {lo} and {hi}")
    return value


def parse_request(packet):
    if packet["protocol"] != PROTOCOL:
        raise ValueError("Incompatible protocol")
    v = Snapshot()
    bounds = {"cash": (0, 100000000), "position": (0, 39), "jail": (-1, 2), "cards": (0, 3),
              "owner": (-1, 1), "level": (0, 5), "mortgaged": (0, 1), "houses": (0, 32),
              "hotels": (0, 12), "turn": (0, 1000000), "active": (0, 1)}
    for f, t in v._fields_:
        if f in ("rejected", "history_size", "history"):
            continue
        x = packet["state"][f]
        if issubclass(t, C.Array):
            if not isinstance(x, list) or len(x) != t._length_:
                raise ValueError(f"Wrong length for {f}")
            setattr(v, f, t(*(integer(n, *bounds[f]) for n in x)))
        else:
            setattr(v, f, integer(x, *bounds[f]))
    behavior = packet["state"].get("behavior", [[], []])
    if not isinstance(behavior, list) or len(behavior) != 2:
        raise ValueError("behavior requires a list for each seat")
    for seat, events in enumerate(behavior):
        if not isinstance(events, list) or len(events) > 16:
            raise ValueError("behavior requires up to 16 public events per seat")
        previous = 0
        for j, e in enumerate(events):
            kind = EVENT_KINDS.index(e["kind"])
            previous = integer(e["turn"], previous, v.turn)
            give, take = (integer(e.get(k, -1), -1, 39) for k in ("give","take"))
            is_bundle = kind >= 6
            square = integer(e.get("square", give if give >= 0 else take), -1 if is_bundle else 0, 39)
            if is_bundle and (square != -1 or give != -1 or take != -1):
                raise ValueError("A bundle observation describes the entire package without scalar deed fields")
            if any(DEED_GROUP[d] < 0 for d in (give,take,square) if d >= 0):
                raise ValueError("Historical actions require deed squares")
            if kind <= 2 and (max(give,take) < 0 or give == take or square != (give if give >= 0 else take)):
                raise ValueError("Trade observation needs distinct traded deeds and a matching square")
            if 3 <= kind <= 5 and (give >= 0 or take >= 0):
                raise ValueError("Purchase observations reference a single square")
            amount = integer(e["amount"], 0 if 3 <= kind <= 5 else -100000000, 100000000)
            v.history[seat][j][:] = [kind,square,amount,previous,give,take,
                                      integer(e.get("generosity",0), -2000, 2000)]
        v.history_size[seat] = len(events)
    history = packet["state"].get("last_rejected", [None, None])
    if not isinstance(history, list) or len(history) != 2:
        raise ValueError("last_rejected needs one entry per proposer")
    for seat, offer in enumerate(history):
        if offer is None:
            continue
        give = integer(offer["give"], -1, 39)
        take = integer(offer["take"], -1, 39)
        if (give == -1 and take == -1) or any(i>=0 and DEED_GROUP[i]<0 for i in (give,take)):
            raise ValueError("Invalid historical trade deeds")
        v.rejected[seat][:] = [give,take,integer(offer["cash"], -100000000,100000000),
                               integer(offer["turn"], 1, v.turn)]
    q = packet["query"]
    kind = KINDS.index(q["kind"])
    square, give, take = (integer(q.get(k, -1), -1, 39) for k in ("square", "give", "take"))
    if kind in (0, 1) and square < 0:
        raise ValueError("A buy or bid request needs a deed")
    for i in (square, give, take):
        if i >= 0 and DEED_GROUP[i] < 0:
            raise ValueError("Expected a deed square")
    who = integer(packet["player"], 0, 1)
    amount = integer(q.get("amount", 0), -2, 100000000)
    for i, group in enumerate(DEED_GROUP):
        if group < 0 and v.owner[i] >= 0:
            raise ValueError("Only deeds can have owners")
        if v.mortgaged[i] and v.owner[i] == -1:
            raise ValueError("A mortgaged deed needs an owner")
        if v.level[i] and (group < 0 or group >= 8 or v.owner[i] == -1 or v.mortgaged[i]):
            raise ValueError("Invalid building ownership")
    if sum(level for level in v.level if level < 5) + v.houses != 32 or v.level[:].count(5) + v.hotels != 12:
        raise ValueError("Building supply does not match the board")
    for deeds in COLOR_DEEDS:
        levels = [v.level[i] for i in deeds]
        if max(levels) and (max(levels)-min(levels) > 1
                            or len({v.owner[i] for i in deeds}) != 1
                            or any(v.mortgaged[i] for i in deeds)):
            raise ValueError("Buildings require an evenly developed, unmortgaged color set")
    if v.cards[0] & v.cards[1] or any(v.jail[i] >= 0 and v.position[i] != 10 for i in range(2)):
        raise ValueError("Invalid jail or held-card state")
    if kind == 0 or (kind == 1 and amount >= 0):
        if v.owner[square] != -1:
            raise ValueError("A purchase or deed auction requires an unowned deed")
    if kind == 1 and amount < 0:
        if v.owner[square] != who or not 0 <= DEED_GROUP[square] < 8:
            raise ValueError("A building bid needs your own street")
    if kind == 2 and square >= 0 and (v.owner[square] != who or not v.mortgaged[square]):
        raise ValueError("Mortgage election requires an owned mortgaged deed")
    if kind == 6 and ((give >= 0 and v.owner[give] != 1-who) or (take >= 0 and v.owner[take] != who)):
        raise ValueError("Trade ownership does not match the offer")
    cash = integer(q.get("cash", 0), -100000000, 100000000)
    if kind == 6:
        if give < 0 and take < 0:
            raise ValueError("A trade requires at least one deed")
        for deed in (give, take):
            if deed >= 0 and DEED_GROUP[deed] < 8 and any(v.level[i] for i in COLOR_DEEDS[DEED_GROUP[deed]]):
                raise ValueError("Sell the color group's buildings before offering its deed")
        def fee(deed):
            return (BOARD[deed]["mortgage"]+9)//10 if deed >= 0 and v.mortgaged[deed] else 0
        if v.cash[who]+cash < fee(give) or v.cash[1-who]-cash < fee(take):
            raise ValueError("The proposed trade exceeds the available cash")
    return v, who, Request(kind, square, amount, give, take, cash)


BOARD = board_info()
DEED_GROUP = [d["group"] for d in BOARD]
COLOR_DEEDS = [[i for i, g in enumerate(DEED_GROUP) if g == color] for color in range(8)]


def choose(strategy, packet):
    import bundle_trade
    if bundle_trade.handles(packet):
        return bundle_trade.choose(strategy,packet)
    v, who, q = parse_request(packet)
    a = Reply()
    lib.mono_decide(config(strategy), C.byref(v), who, C.byref(q), C.byref(a))
    return dict(protocol=PROTOCOL, game_id=packet["game_id"], request_id=packet["request_id"],
                action={f: getattr(a, f) for f, _ in a._fields_})

def evaluate_trade(strategy, packet):
    import bundle_trade
    if bundle_trade.handles(packet):
        return bundle_trade.assess(strategy,packet)
    if strategy[0] < 6:
        raise ValueError("Trade evaluation requires a neural model")
    v, who, q = parse_request(packet)
    if KINDS[q.kind] != "accept":
        raise ValueError("Use an accept query describing the offer received by player")
    scores, accepted = (C.c_float*8)(), INT()
    if not lib.mono_assess(config(strategy), C.byref(v), who, C.byref(q), scores, C.byref(accepted)):
        raise ValueError("Illegal trade")
    result = {
        "game_id": packet["game_id"], "request_id": packet["request_id"],
        "decision": "accept" if accepted.value else "decline",
        "proposer": {"player": 1-who, "value_before": scores[0], "value_after": scores[1]},
        "recipient": {"player": who, "value_before": scores[2], "value_after": scores[3]},
        "estimated_acceptance": scores[4], "expected_proposer_gain": scores[5],
    }
    if strategy[0] >= 9 and strategy[8] is not None:
        result.update(predicted_recipient_gain_if_accepted=scores[6], used_deal_model=bool(scores[7]))
    return result


def scout(strategy, packet):
    if strategy[0] < 7:
        raise ValueError("Opponent estimates require the adaptive network")
    v, who, _ = parse_request(packet)
    estimates = (C.c_float*2)()
    lib.mono_scout(config(strategy), C.byref(v), 1-who, estimates)
    return dict(opponent=1-who, observed_events=v.history_size[1-who],
                refusal_probability=estimates[0], aggressive_acquisition_probability=estimates[1])

def bundle_batch(a,b,seed,games,cap):
    names = ("wins","losses","caps","turns","offers","accepted","package_offers",
             "package_accepted","multi_deed_accepted","card_accepted")
    values = (C.c_int64*len(names))()
    run = lib.mono_bundle_matches
    run.argtypes = [Config,Config,C.c_uint64,INT,INT,ptr(C.c_int64)]
    run.restype = INT
    if not run(config(a),config(b),seed,games,cap,values):
        raise ValueError("Package matches require a neural player and legal settlement")
    return dict(zip(names, values))
