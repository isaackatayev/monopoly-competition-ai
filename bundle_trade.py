"""Optional deed, held release card, and cash extension for Monopoly decision packets."""
import ctypes as C

import native

CAPABILITY = "multi_deed_trades_v1"
DEED_FIELDS = {"give_deeds", "take_deeds"}
CARD_NAMES = ("chance", "chest")
ARRAY_FIELDS = DEED_FIELDS | {"give_cards", "take_cards"}
QUERY_FIELDS = ARRAY_FIELDS | {"kind", "give", "take", "square", "cash", "amount", "value"}


class BundleResult(C.Structure):
    _fields_ = [
        ("give", C.c_uint64), ("take", C.c_uint64), ("cash", C.c_int), ("accept", C.c_int),
        ("fee", C.c_int * 2), ("cash_after", C.c_int * 2), ("full_colors", C.c_int * 2),
        ("cards_after", C.c_int * 2),
        ("before", C.c_float), ("after", C.c_float), ("willingness", C.c_float), ("gain", C.c_float),
        ("deal_gain", C.c_float), ("scalar", C.c_int), ("used_deal_model", C.c_int),
    ]


def handles(packet):
    """Raise on ambiguous array packets before the old scalar parser can ignore them."""
    query = packet["query"]
    has_arrays = bool(ARRAY_FIELDS.intersection(query))
    capabilities = packet.get("capabilities", [])
    enabled = isinstance(capabilities, list) and CAPABILITY in capabilities
    if has_arrays and (not enabled or query["kind"] not in ("offer", "accept")):
        raise ValueError("Bundle deed arrays require a trade query and multi_deed_trades_v1")
    return enabled and (query["kind"] == "offer" or has_arrays)


def _mask(items):
    if not isinstance(items, list) or len(items) > 28:
        raise ValueError("Each bundle side requires a list of up to 28 deed squares")
    mask = 0
    for value in items:
        square = native.integer(value, 0, 39)
        if native.DEED_GROUP[square] < 0 or mask & (1 << square):
            raise ValueError("A bundle must contain distinct deed squares")
        mask |= 1 << square
    return mask


def _card_mask(values):
    if not isinstance(values, list) or len(values) > 2:
        raise ValueError("Expected a list of held release cards")
    mask = 0
    for name in values:
        if name not in CARD_NAMES:
            raise ValueError("Unknown held release card")
        bit = 1 << (40 + CARD_NAMES.index(name))
        if mask & bit:
            raise ValueError("Repeated release card")
        mask |= bit
    return mask


def _cards(mask):
    return [name for idx, name in enumerate(CARD_NAMES) if mask & (1 << idx)]


def _squares(mask):
    return [sq for sq in range(40) if mask & (1 << sq)]


def _packet(packet):
    if not handles(packet):
        raise ValueError("This request does not use the multi-deed trade extension")
    for name in ("game_id", "request_id"):
        value = packet[name]
        if type(value) not in (int, str) or (isinstance(value, str) and not 0 < len(value) <= 256):
            raise ValueError("Expected a competition identifier")
    query = packet["query"]
    if set(query) - QUERY_FIELDS:
        raise ValueError("Bundle trades support deeds, held release cards and immediate cash only")
    for key in ("give", "take", "square"):
        native.integer(query.get(key, -1), -1, -1)
    native.integer(query.get("amount", 0), 0, 0)
    if DEED_FIELDS.intersection(query) and not DEED_FIELDS.issubset(query):
        raise ValueError("Specify both give_deeds and take_deeds")
    give = _mask(query.get("give_deeds", [])) | _card_mask(query.get("give_cards", []))
    take = _mask(query.get("take_deeds", [])) | _card_mask(query.get("take_cards", []))
    cash = native.integer(query.get("cash", 0), -100000000, 100000000)
    if give & take:
        raise ValueError("An asset cannot appear on both sides")
    if not (give or take) and cash:
        raise ValueError("Cash-only terms are outside the bundle format")
    if query["kind"] == "offer" and (give or take or cash):
        raise ValueError("An offer request asks the bot to propose new terms")
    if "value" in query:
        has_deed = int(bool(give or take))
        native.integer(query["value"], has_deed, has_deed)
    # The established validator checks the entire public snapshot. Its scalar
    # ownership/fee checks are replaced by joint native settlement for bundles.
    v, who, _ = native.parse_request({**packet, "query": {"kind": "offer"}})
    return v, who, give, take, cash


def _evaluate(strategy, packet):
    v, who, give, take, cash = _packet(packet)
    if not native.config(strategy).net:
        raise ValueError("Bundle evaluation requires a neural competition model")
    result = BundleResult()
    function = native.lib.mono_bundle_assess
    function.argtypes = [native.Config, C.POINTER(native.Snapshot), C.c_int, C.c_uint64,
                         C.c_uint64, C.c_int, C.POINTER(BundleResult)]
    function.restype = C.c_int
    if not function(native.config(strategy), C.byref(v), 1-who, give, take, cash, C.byref(result)):
        raise ValueError("Illegal deed or card ownership, developed color group, or insufficient cash for terms and fees")
    return who, result


def _envelope(packet, value, give, take, cash, give_cards=(), take_cards=()):
    return dict(protocol=native.PROTOCOL, game_id=packet["game_id"], request_id=packet["request_id"],
                action=dict(value=value, square=-1, give=-1, take=-1, cash=cash,
                            give_deeds=give, take_deeds=take, give_cards=list(give_cards), take_cards=list(take_cards)))


def choose(strategy, packet):
    if packet["query"]["kind"] == "accept":
        q = packet["query"]
        if native.config(strategy).net:
            _, result = _evaluate(strategy, packet)
            accepted, cash = result.accept, result.cash
        else:
            _, _, give, take, cash = _packet(packet)
            if (give | take) >> 40 or (give & (give-1)) or (take & (take-1)):
                raise ValueError("Multi-asset evaluation requires a neural competition model")
            accepted = 0
            if give or take:
                scalar = {"kind": "accept", "give": give.bit_length()-1,
                          "take": take.bit_length()-1, "cash": cash}
                accepted = native.choose(strategy, {**packet, "query": scalar})["action"]["value"]
        return _envelope(packet, accepted, list(q.get("give_deeds", [])),
                         list(q.get("take_deeds", [])), cash, q.get("give_cards", []), q.get("take_cards", []))
    v, who, _, _, _ = _packet(packet)
    if not native.config(strategy).net:
        legacy = native.choose(strategy, {**packet, "capabilities": [], "query": {"kind": "offer"}})["action"]
        give = [legacy["give"]] if legacy["give"] >= 0 else []
        take = [legacy["take"]] if legacy["take"] >= 0 else []
        return _envelope(packet, int(bool(give or take)), give, take, legacy["cash"])
    function = native.lib.mono_bundle_offer
    function.argtypes = [native.Config, C.POINTER(native.Snapshot), C.c_int, C.POINTER(BundleResult)]
    function.restype = C.c_int
    result = BundleResult()
    if not function(native.config(strategy), C.byref(v), who, C.byref(result)):
        raise ValueError("Could not evaluate bundle proposals")
    return _envelope(packet, int(bool(result.give or result.take)), _squares(result.give),
                     _squares(result.take), result.cash, _cards(result.give >> 40), _cards(result.take >> 40))


def assess(strategy, packet):
    if packet["query"]["kind"] != "accept":
        raise ValueError("Use an accept query to assess an incoming bundle")
    who, result = _evaluate(strategy, packet)
    color_sets = [[g for g in range(8) if bits & (1 << g)] for bits in result.full_colors]
    report = dict(
        game_id=packet["game_id"], request_id=packet["request_id"],
        decision="accept" if result.accept else "decline",
        proposer=dict(player=1-who, value_before=result.before, value_after=result.after),
        recipient=dict(player=who, value_before=C.c_float(1-result.before).value,
                       value_after=C.c_float(1-result.after).value),
        mortgage_interest=list(result.fee), cash_after=list(result.cash_after), full_colors_after=color_sets,
        cards_after=[_cards(mask) for mask in result.cards_after],
        estimated_acceptance=result.willingness, expected_proposer_gain=result.gain,
        acceptance_method="scalar_response_network" if result.scalar else "bundle_asset_heuristic",
    )
    if result.scalar and strategy[0] >= 9 and strategy[8] is not None:
        report.update(predicted_recipient_gain_if_accepted=result.deal_gain,
                      used_deal_model=bool(result.used_deal_model))
    return report
