import copy
import unittest
from unittest.mock import patch

from arena import example
from bundle_trade import CAPABILITY
from live_player import LivePlayer
import native


def request(kind, sparse=True):
    packet = example("offer" if kind=="offer" else "trade")
    packet.update(opponent_id="visitor", capabilities=[CAPABILITY], request_id=1)
    packet["query"] = {"kind":kind}
    packet["state"]["cards"] = [1,2]
    if sparse:
        del packet["state"]["behavior"]
    return packet


def next_request(packet, number, kind="manage"):
    result = copy.deepcopy(packet)
    result.update(request_id=number, query={"kind":kind})
    return result


class BundleHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = native.model("adaptive-v1")

    def test_incoming_package_and_replay(self):
        for expected, give, take in ((True,[16,18,27],[]),(False,[],[15,19,26])):
            bot = LivePlayer(self.strategy)
            msg = request("accept")
            msg["query"].update(give_deeds=give,take_deeds=take,cash=-1)
            with self.subTest(expected=expected):
                reply = bot.choose(msg)
                self.assertEqual(bool(reply["action"]["value"]), expected)
                self.assertEqual(bot.choose(copy.deepcopy(msg)), reply)
                history = bot.games[("demo",1)].history
                for seat,event_kind in enumerate(("proposed_bundle", "accepted_bundle" if expected else "declined_bundle")):
                    self.assertEqual(history[seat], [dict(kind=event_kind,amount=-1,turn=29,generosity=0)])
                changed = copy.deepcopy(msg)
                changed["query"]["cash"] = -2
                with self.assertRaisesRegex(ValueError,"reused"):
                    bot.choose(changed)

    def test_sparse_full_transfer_and_ambiguous_snapshots(self):
        # Force one valid proposal; confirmations still pass through the actual native player.
        terms = dict(value=1,square=-1,give=-1,take=-1,give_deeds=[16,27],take_deeds=[19],
                     give_cards=["chance"],take_cards=["chest"],cash=75)
        for case,expected in (("all","accepted_bundle"),("unchanged","declined_bundle"),
                              ("partial",None),("late",None),("extra",None),("missing-card",None)):
            with self.subTest(case=case):
                bot = LivePlayer(self.strategy)
                opening = request("offer")
                with patch("native.choose",return_value={"action":terms}):
                    bot.choose(opening)
                current = next_request(opening,2)
                state = current["state"]
                if case!="unchanged":
                    state["owner"][16] = 1
                    if case!="partial":
                        state["owner"][27] = 1
                        state["owner"][19] = 0
                        state["cash"][0] -= 75
                        state["cash"][1] += 75
                        state["cards"] = [2,1]
                if case=="late":
                    state["turn"] += 1
                if case=="extra":
                    state["owner"][5] = 1
                if case=="missing-card":
                    state["cards"] = [0,1]
                bot.choose(current)
                bot.choose(current)
                history = bot.games[("demo",0)].history
                self.assertEqual(history[0], [dict(kind="proposed_bundle",amount=75,turn=29,generosity=0)])
                self.assertEqual([e["kind"] for e in history[1]], [expected] if expected else [])
                self.assertIsNone(bot.games[("demo",0)].bundle_pending)

    def test_card_transfer_and_mortgage_election_confirm_only_a_settled_package(self):
        terms = dict(value=1,square=-1,give=-1,take=-1,give_deeds=[],take_deeds=[19],
                     give_cards=["chance"],take_cards=[],cash=10)
        bot = LivePlayer(self.strategy)
        opening = request("offer")
        opening["state"]["mortgaged"][19] = 1
        with patch("native.choose", return_value={"action":terms}):
            bot.choose(opening)
        election = next_request(opening,2)
        election["query"]["square"] = 19
        election["state"]["owner"][19] = 0
        # Intermediate state with the deed, before the card transfer completes.
        bot.choose(election)
        self.assertFalse(bot.games[("demo",0)].history[1])
        election["request_id"] = 3
        election["state"]["cards"] = [0,3]
        bot.choose(election)
        self.assertEqual(bot.games[("demo",0)].history[1][0]["kind"],"accepted_bundle")

    def test_authoritative_feed_supersedes_inference_and_cross_game(self):
        bot = LivePlayer(self.strategy)
        p = request("offer")
        reply = bot.choose(p)
        self.assertEqual(reply["action"]["value"],1)
        current = next_request(p,2)
        refusal = dict(kind="declined_bundle",amount=-25,turn=29,generosity=220)
        current["state"]["behavior"] = [[],[refusal]]
        bot.choose(current)
        current["request_id"] = 3
        del current["state"]["behavior"]
        bot.choose(current)
        self.assertEqual(bot.games[("demo",0)].history,[[],[refusal]])
        rematch = request("accept")
        rematch.update(game_id="rematch",previous_game_id="demo")
        rematch["query"].update(give_cards=["chance"],cash=-25)
        bot.choose(rematch)
        self.assertEqual(bot.games[("rematch",1)].prior,[{**refusal,"turn":0}])
        rematch.update(game_id="stranger",opponent_id="different")
        bot.choose(rematch)
        self.assertFalse(bot.games[("stranger",1)].prior)

    def test_reported_proposal_uses_host_generosity_and_no_duplicate(self):
        bot = LivePlayer(self.strategy)
        msg = request("accept",sparse=False)
        msg["query"].update(give_cards=["chance"],cash=-10)
        event = dict(kind="proposed_bundle",amount=-10,turn=29,generosity=90)
        msg["state"]["behavior"] = [[event],[]]
        bot.choose(msg)
        obs = bot.games[("demo",1)].history
        self.assertEqual(obs[0],[event])
        self.assertEqual(obs[1][0]["generosity"],90)

    def test_scalar_array_preserves_native_trade_event_and_empty_proposal_passes(self):
        bot = LivePlayer(self.strategy)
        msg = request("accept")
        msg["query"].update(give_deeds=[27],take_deeds=[19],cash=-90)
        bot.choose(msg)
        history = bot.games[("demo",1)].history
        self.assertEqual((history[0][0]["kind"],history[0][0]["give"],history[0][0]["take"]),
                         ("proposed_trade",27,19))
        self.assertIn(history[1][0]["kind"],("accepted_trade","declined_trade"))
        empty = request("accept")
        empty.update(game_id="empty")
        empty["query"].update(give_deeds=[],take_deeds=[])
        bot.choose(empty)
        self.assertEqual(bot.games[("empty",1)].history,[[],[]])

    def test_invalid_packet_does_not_poison_retry(self):
        bot = LivePlayer(self.strategy)
        msg = request("accept")
        msg["query"].update(give_deeds=[16,18],take_deeds=[])
        bot.choose(msg)
        match = copy.deepcopy(bot.games[("demo",1)])
        bad = next_request(msg,2,"accept")
        bad["state"]["turn"] += 4
        bad["query"].update(give_deeds=[26,35],take_deeds=[])
        with self.assertRaises(ValueError):
            bot.choose(bad)
        self.assertEqual(bot.games[("demo",1)],match)
        good = next_request(msg,2)
        bot.choose(good)


if __name__=="__main__":
    unittest.main()
