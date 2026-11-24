"""Select a neural checkpoint between linked matches using observed game results."""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import random

from live_player import LivePlayer
import native


def update(evidence, played, won):
    # Discount past matches so a change of rival strategy can be learned.
    return tuple((.985*w + int(i==played and won), .985*l + int(i==played and not won))
                 for i,(w,l) in enumerate(evidence))


def select(evidence, game, seat, opponent):
    seed = hashlib.sha256(json.dumps([game,seat,opponent], separators=(",",":")).encode()).digest()
    rng = random.Random(seed)
    return max(range(len(evidence)), key=lambda i: rng.betavariate(1+evidence[i][0], 1+evidence[i][1]))


@dataclass
class Entry:
    opponent: object
    profile: int
    evidence: tuple
    previous: object = None
    reported: tuple = ()
    result: tuple = ()  # Empty until the host reports the outcome, including a possible tie.


class SeriesPlayer:
    def __init__(self, profiles):
        self.profiles = tuple(profiles)
        self.player = LivePlayer(profiles[0])
        self.games = OrderedDict()

    def choose(self, packet):
        game, seat = packet["game_id"], native.integer(packet["player"],0,1)
        opponent, previous = packet.get("opponent_id"), packet.get("previous_game_id")
        result = (packet["previous_winner"],) if "previous_winner" in packet else ()
        if result and (previous is None or opponent is None or
                       (result[0] is not None and (type(result[0]) is not int or result[0] not in (0,1)))):
            raise ValueError("A result requires a linked previous game, opponent ID and winner seat or null")
        key = (game,seat)
        entry = self.games.get(key)
        parent = None
        if entry:
            if previous is not None and previous != entry.previous:
                raise ValueError("The previous game changed during this match")
            if result and result != entry.reported:
                raise ValueError("The reported previous result changed during this match")
        else:
            evidence = ((0.,0.),)*len(self.profiles)
            profile = 0
            if previous is not None:
                matches = [(s,self.games[previous,s]) for s in (0,1) if
                           (previous,s) in self.games and self.games[previous,s].opponent == opponent]
                if len(matches)==1:
                    old_seat,parent = matches[0]
                    evidence,profile = parent.evidence,parent.profile
                    if result:
                        if parent.result and parent.result!=result:
                            raise ValueError("Conflicting outcome for a completed game")
                        evidence = update(evidence, profile, result[0] == old_seat)
                        profile = select(evidence,game,seat,opponent)
            entry = Entry(opponent,profile,evidence,previous,result)
        # Commit only after a valid decision. A replay retains the chosen checkpoint.
        reply = self.player.choose(packet,self.profiles[entry.profile])
        if parent is not None and result:
            parent.result = result
        self.games[key] = entry
        self.games.move_to_end(key)
        if len(self.games)>128:
            self.games.popitem(last=False)
        return reply


def competition_player(name, adapt_series=False):
    initial = native.model(name)
    if not adapt_series:
        return LivePlayer(initial)
    if initial[0]<7:
        raise ValueError("Series learning needs an adaptive neural checkpoint")
    profiles = [initial]
    for alternative in ("adaptive-v1","adaptive-v3","pressure"):
        candidate = native.model(alternative)
        if all(s[0] != candidate[0] for s in profiles):
            profiles.append(candidate)
    return SeriesPlayer(profiles)
