"""Keep confirmed public observations when a competition sends board snapshots only."""
from collections import OrderedDict
import ctypes as C
import hashlib
import json
from dataclasses import dataclass, field, replace

import native


@dataclass
class Match:
    history: list = field(default_factory=lambda: [[], []])
    seen_rejections: list = field(default_factory=lambda: [None, None])
    pending: dict = field(default_factory=dict)
    bundle_pending: tuple = None
    replies: OrderedDict = field(default_factory=OrderedDict)
    turn: int = -1
    opponent_id: object = None
    prior: list = field(default_factory=list)

    def observe(self, seat, event):
        events = self.history[seat]
        if not events or events[-1]["turn"] <= event["turn"]:
            events.append(event)
            del events[:-16]


def trade_event(v, seat, give, take, cash, turn, kind):
    generosity = native.lib.mono_generosity(C.byref(v), seat, give, take, cash)
    return dict(kind=kind, square=give if give >= 0 else take, give=give, take=take,
                amount=cash, turn=turn, generosity=generosity)


def offer_event(v, proposer, terms):
    """Choose the same trade observation as the local match engine."""
    give = terms.get("give_deeds", [])
    take = terms.get("take_deeds", [])
    cash = terms.get("cash", 0)
    if terms.get("give_cards") or terms.get("take_cards") or len(give)>1 or len(take)>1:
        return dict(kind="proposed_bundle", amount=cash, turn=v.turn, generosity=0)
    g = give[0] if give else terms.get("give",-1)
    t = take[0] if take else terms.get("take",-1)
    if g >= 0 or t >= 0:
        return trade_event(v, proposer, g, t, cash, v.turn, "proposed_trade")
    return None


def ownership(v):
    return tuple(v.owner), tuple(v.cards)


def bundle_transfer(v, proposer, terms):
    owners, cards = list(v.owner), list(v.cards)
    for direction, receiver in (("give",1-proposer), ("take",proposer)):
        for sq in terms.get(direction+"_deeds", []):
            owners[sq] = receiver
        for name in terms.get(direction+"_cards", []):
            bit = 1 if name=="chance" else 2
            cards[1-receiver] &= ~bit
            cards[receiver] |= bit
    return tuple(owners), tuple(cards)


def same_observation(a,b):
    return all(a.get(key,-1)==b.get(key,-1) for key in ("kind","turn","amount","give","take"))


class LivePlayer:
    def __init__(self, strategy):
        self.strategy = strategy
        self.games = OrderedDict()

    def choose(self, packet, strategy=None):
        strategy = self.strategy if strategy is None else strategy
        if strategy[0] < 7:
            return native.choose(strategy, packet)
        import bundle_trade
        structured_trade = bundle_trade.handles(packet)
        if structured_trade:
            v, who, _, _, _ = bundle_trade._packet(packet)
            q = native.Request(native.KINDS.index("offer"),-1,0,-1,-1,0)
        else:
            v, who, q = native.parse_request(packet)
        incoming = packet["query"]["kind"]=="accept"
        key, request = (packet["game_id"], who), packet["request_id"]
        for identifier in (key[0], request):
            if type(identifier) not in (int, str) or identifier == "":
                raise ValueError("Invalid competition identifier")
        opponent_id = packet.get("opponent_id")
        previous_game = packet.get("previous_game_id")
        for identifier in (opponent_id, previous_game):
            if identifier is not None and (type(identifier) not in (int, str) or identifier == ""):
                raise ValueError("Invalid series identifier")
        if previous_game is not None and (previous_game == key[0] or opponent_id is None):
            raise ValueError("A previous game requires a distinct ID and a named opponent")
        match = self.games.get(key)
        if match is None:
            match = Match(opponent_id=opponent_id)
            if previous_game is not None:
                # The host explicitly identifies the completed game and the same rival.
                for seat in (0,1):
                    old = self.games.get((previous_game,seat))
                    if old is not None and old.opponent_id == opponent_id:
                        trades = [e for e in old.history[1-seat] if e["kind"] in native.EVENT_KINDS[:3]+native.EVENT_KINDS[6:9]]
                        match.prior = [{**e, "turn":0} for e in trades[-6:]]
                        break
        elif opponent_id is not None and match.opponent_id != opponent_id:
            raise ValueError("Opponent ID changed within an active match")
        digest = hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",",":")).encode()).digest()
        if request in match.replies:
            original, reply = match.replies[request]
            if original != digest:
                raise ValueError("Decision ID was reused with different contents")
            self.games.move_to_end(key)
            return reply
        if v.turn < match.turn:
            raise ValueError("Out-of-order turn in active match")
        # Stage observations until the complete packet has passed native legality
        # and a reply exists. A malformed request cannot alter a later retry.
        match = replace(match, history=[list(h) for h in match.history],
                        seen_rejections=list(match.seen_rejections), pending=dict(match.pending))
        match.turn = v.turn
        proposal = offer_event(v,1-who,packet["query"]) if incoming else None
        state = packet["state"]
        if "behavior" in state:
            match.history = self._public_history(match, state["behavior"], who, proposal)
            match.pending.clear()
            match.bundle_pending = None
            match.seen_rejections = list(state.get("last_rejected", [None, None]))
        else:
            self._collect(match, state, v, who, q)
        if proposal:
            observed = [e for e in match.history[1-who] if same_observation(e,proposal)]
            if not observed:
                match.observe(1-who,proposal)
            elif len(observed)==1 and "generosity" in observed[0]:
                proposal = {**proposal,"generosity":observed[0]["generosity"]}
        history = list(match.history)
        history[1-who] = (match.prior + history[1-who])[-16:]
        enriched = {**packet, "state": {**state, "behavior": history}}
        reply = native.choose(strategy, enriched)
        action = reply["action"]
        if packet["query"]["kind"]=="offer":
            match.pending.pop(who,None)
            match.bundle_pending = None
            offered = offer_event(v, who, action)
            if offered:
                match.observe(who,offered)
                if offered["kind"]=="proposed_bundle":
                    match.bundle_pending = (offered,ownership(v),bundle_transfer(v,who,action))
                else:
                    match.pending[who] = offered
        elif proposal:
            kind = ("accepted_" if action["value"] else "declined_")+proposal["kind"][len("proposed_"):]
            answer = {**proposal,"kind":kind}
            if not any(same_observation(e,answer) for e in match.history[who]):
                match.observe(who,answer)
        self.games[key] = match
        self.games.move_to_end(key)
        if len(self.games)>128:
            self.games.popitem(last=False)
        match.replies[request] = (digest, reply)
        if len(match.replies) > 128:
            match.replies.popitem(last=False)
        return reply

    @staticmethod
    def _public_history(match, histories, who, proposal):
        # Hosts need only publish the terms. Recover pre-trade valuations if this
        # process saw the proposal when ownership and group counts were current.
        values = {}
        for seat, events in enumerate(match.history):
            for e in events:
                if e["kind"] == "proposed_trade" and "generosity" in e:
                    values[(seat,e["turn"],e.get("give",-1),e.get("take",-1),e["amount"])] = e["generosity"]
        if proposal and proposal["kind"]=="proposed_trade":
            values[(1-who,proposal["turn"],proposal["give"],proposal["take"],proposal["amount"])] = proposal["generosity"]
        recorded = [[],[]]
        for seat, events in enumerate(histories):
            for e in events:
                if "generosity" not in e and e["kind"] in native.EVENT_KINDS[:3]:
                    proposer = seat if e["kind"] == "proposed_trade" else 1-seat
                    terms = (proposer,e["turn"],e.get("give",-1),e.get("take",-1),e["amount"])
                    if terms in values:
                        e = {**e,"generosity":values[terms]}
                recorded[seat].append(e)
        return recorded

    @staticmethod
    def _collect(match, state, v, who, q):
        for proposer, refusal in enumerate(state.get("last_rejected", [None, None])):
            if refusal and refusal != match.seen_rejections[proposer]:
                give,take,cash,turn = (refusal[k] for k in ("give","take","cash","turn"))
                event = match.pending.get(proposer)
                if event and (event["give"],event["take"],event["amount"],event["turn"]) == (give,take,cash,turn):
                    event = {**match.pending.pop(proposer), "kind":"declined_trade"}
                else:
                    # Without the original board the offer's historical generosity is unknown.
                    event = dict(kind="declined_trade", square=give if give>=0 else take, give=give,
                                 take=take, amount=cash, turn=turn, generosity=0)
                repeated = any(e["kind"] == "declined_trade" and e["turn"] == turn
                               and e.get("give",-1) == give and e.get("take",-1) == take
                               and e["amount"] == cash for e in match.history[1-proposer])
                if not repeated:
                    match.observe(1-proposer, event)
            match.seen_rejections[proposer] = refusal
        pending = match.pending.get(who)
        if pending:
            give,take = pending["give"],pending["take"]
            if (v.turn <= pending["turn"]+1 and
                    (give < 0 or v.owner[give] == 1-who) and (take < 0 or v.owner[take] == who)):
                match.observe(1-who, {**pending, "kind":"accepted_trade"})
                del match.pending[who]
            elif (v.turn == pending["turn"] and q.kind == native.KINDS.index("manage") and q.square < 0
                  and (give < 0 or v.owner[give] == who) and (take < 0 or v.owner[take] == 1-who)):
                # Under the synchronous duel protocol, ordinary management starts after the
                # offer is settled and before another deal can reverse the deed transfer.
                match.observe(1-who, {**pending, "kind":"declined_trade"})
                del match.pending[who]
            elif v.turn > pending["turn"]+1:
                del match.pending[who]
        if match.bundle_pending is not None:
            event,before,after = match.bundle_pending
            # The synchronous pre-roll management request settles the preceding
            # offer. Later ownership can also reflect another turn or a used card.
            if v.turn!=event["turn"]:
                match.bundle_pending = None
            elif v.active == who:
                current = ownership(v)
                if current == after:
                    match.observe(1-who, {**event, "kind":"accepted_bundle"})
                    match.bundle_pending = None
                elif q.kind == native.KINDS.index("manage") and q.square < 0:
                    if current == before:
                        match.observe(1-who, {**event, "kind":"declined_bundle"})
                    match.bundle_pending = None
        # Only a voluntary decline before a deed auction is attributable from the request alone.
        if (q.kind == native.KINDS.index("bid") and q.amount >= 0 and v.active != who
                and v.position[v.active] == q.square and v.cash[v.active] >= native.BOARD[q.square]["price"]):
            event = dict(kind="declined_face",square=q.square,amount=native.BOARD[q.square]["price"],turn=v.turn)
            match.observe(v.active, event)
