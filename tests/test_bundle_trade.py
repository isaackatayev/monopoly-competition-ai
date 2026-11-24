import copy
import json
import subprocess
import unittest

from arena import example
from bundle_trade import CAPABILITY
from live_player import LivePlayer
import native


def packet(give=(), take=(), cash=0, give_cards=(), take_cards=()):
    message = example("trade")
    message["capabilities"] = [CAPABILITY]
    message["query"] = dict(kind="accept", give_deeds=list(give), take_deeds=list(take),
                            give_cards=list(give_cards), take_cards=list(take_cards), cash=cash)
    return message


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = native.model("champion")

    def test_full_afterstate_matches_independent_public_snapshot(self):
        msg = packet([16, 18, 27], [19, 26], 125, ["chance"], ["chest"])
        state = msg["state"]
        state["cards"] = [1, 2]
        state["mortgaged"][16] = state["mortgaged"][27] = state["mortgaged"][26] = 1
        before = copy.deepcopy(msg)
        result = native.evaluate_trade(self.strategy,msg)
        self.assertEqual(msg,before)
        # Received 26 costs $13 and received 16 + 27 cost $9 + $13.
        self.assertEqual(result["mortgage_interest"], [13, 22])
        self.assertEqual(result["cash_after"], [662, 923])
        self.assertEqual(result["cards_after"], [["chest"], ["chance"]])
        # Both seats exchange different deeds in each of these colors.
        self.assertEqual(result["full_colors_after"], [[], []])
        fresh = packet()
        fresh["state"] = copy.deepcopy(state)
        for sq in (16, 18, 27):
            fresh["state"]["owner"][sq] = 1
        for sq in (19, 26):
            fresh["state"]["owner"][sq] = 0
        fresh["state"]["cards"] = [2, 1]
        fresh["state"]["cash"] = [662, 923]
        independently_scored = native.evaluate_trade(self.strategy,fresh)
        for seat in ("proposer","recipient"):
            self.assertAlmostEqual(result[seat]["value_after"],independently_scored[seat]["value_before"],places=6)
        reply = native.choose(self.strategy,msg)["action"]
        self.assertEqual(reply["give_deeds"],[16,18,27])
        self.assertEqual(reply["value"] == 1,result["decision"] == "accept")

    def test_two_simultaneous_color_completions_and_all_four_railroads(self):
        msg = packet([27, 35], [19, 15])
        result = native.evaluate_trade(self.strategy,msg)
        self.assertEqual(result["full_colors_after"], [[3], [5]])
        for sq in (5,15,25,35):
            msg["state"]["owner"][sq] = 0
        msg["query"] = {"kind":"accept","give_deeds":[5,15,25,35],"take_deeds":[]}
        self.assertEqual(native.evaluate_trade(self.strategy,msg)["decision"], "accept")

    def test_joint_funding_each_recipient_and_cash_direction(self):
        msg = packet([16,27],[26],5)
        s = msg["state"]
        s["cash"] = [18, 17]
        for sq in (16,27,26):
            s["mortgaged"][sq] = 1
        result = native.evaluate_trade(self.strategy,msg)
        self.assertEqual(result["cash_after"],[0,0])
        for seat in (0,1):
            broken = copy.deepcopy(msg)
            broken["state"]["cash"][seat] -= 1
            with self.assertRaises(ValueError):
                native.choose(self.strategy,broken)
        msg["query"]["cash"] = -5
        s["cash"] = [8,27]
        self.assertEqual(native.evaluate_trade(self.strategy,msg)["cash_after"],[0,0])

    def test_ownership_group_building_and_invalid_terms(self):
        msg = packet([27],[19])
        for change in ({"give_deeds":[27,27]}, {"take_deeds":[16]}, {"give_deeds":[0]},
                       {"give_deeds":[True]}, {"give_deeds":[27,19]}, {"give":27},
                       {"give_deeds":"27"}, {"give_deeds":[27,26]}, {"give_cards":["chance"]},
                       {"take_cards":["chest","chest"]}, {"give_cards":["jail"]},
                       {"give_cards":["chance"],"take_cards":["chance"]}, {"future_rent":40}):
            broken = copy.deepcopy(msg)
            broken["query"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                native.choose(self.strategy,broken)
        msg["state"]["owner"][19] = 0
        msg["state"]["level"][18] = 1
        msg["state"]["houses"] = 31
        msg["query"].update(give_deeds=[16,27],take_deeds=[])
        with self.assertRaises(ValueError):
            native.choose(self.strategy,msg)

    def test_feature_gate_pass_and_migration(self):
        ordinary = example("trade")
        self.assertEqual(native.choose(self.strategy,ordinary),
                         native.choose(self.strategy,{**ordinary,"capabilities":[CAPABILITY]}))
        self.assertNotIn("give_deeds",native.choose(self.strategy,ordinary)["action"])
        empty = packet()
        reply = native.choose(self.strategy,empty)["action"]
        self.assertEqual((reply["value"],reply["give_deeds"],reply["take_deeds"],reply["cash"]),(0,[],[],0))
        for changed in ({"capabilities":[]},{"capabilities":CAPABILITY}):
            with self.assertRaises(ValueError):
                native.choose(self.strategy,{**empty,**changed})
        for name in ("give_deeds","take_deeds"):
            broken = copy.deepcopy(empty)
            del broken["query"][name]
            with self.assertRaises(ValueError):
                native.choose(self.strategy,broken)
        empty["query"]["cash"] = 75
        with self.assertRaises(ValueError):
            native.choose(self.strategy,empty)
        opening = example("buy")
        opening.update(capabilities=[CAPABILITY],query={"kind":"offer"})
        self.assertEqual(native.choose(self.strategy,opening)["action"]["value"],0)
        self.assertEqual(native.choose(native.model("legacy"),opening)["action"]["give_deeds"],[])

    def test_array_encoded_scalar_deals_match_production(self):
        for cash in (-90, 75):
            ordinary = example("trade")
            ordinary["query"]["cash"] = cash
            structured = packet([27],[19],cash)
            scalar = native.evaluate_trade(self.strategy,ordinary)
            extended = native.evaluate_trade(self.strategy,structured)
            for key in scalar:
                self.assertEqual(extended[key],scalar[key])
            self.assertEqual(native.choose(self.strategy,ordinary)["action"]["value"],
                             native.choose(self.strategy,structured)["action"]["value"])
            self.assertEqual(extended["acceptance_method"],"scalar_response_network")
            legacy = native.model("legacy")
            self.assertEqual(native.choose(legacy,structured)["action"]["value"],
                             native.choose(legacy,ordinary)["action"]["value"])

    def test_card_sale_and_replay_contract(self):
        opening = example("buy")
        opening.update(capabilities=[CAPABILITY],query={"kind":"offer"},game_id=4382,request_id="offer.1")
        opening["state"]["cards"] = [1,0]
        player = LivePlayer(self.strategy)
        offered = player.choose(opening)
        action = offered["action"]
        self.assertEqual((offered["game_id"],offered["request_id"]),(4382,"offer.1"))
        self.assertEqual(action["give_cards"],["chance"])
        self.assertEqual(action["give_deeds"],[])
        self.assertLess(action["cash"],0)
        self.assertEqual(offered,player.choose(copy.deepcopy(opening)))
        incoming = {**opening,"player":1,"request_id":"accept.1","query":{**action,"kind":"accept"}}
        self.assertEqual(native.evaluate_trade(self.strategy,incoming)["cards_after"],[[],["chance"]])
        self.assertEqual(player.choose(incoming)["request_id"],"accept.1")
        with self.assertRaisesRegex(ValueError,"reused"):
            player.choose({**incoming,"query":{**incoming["query"],"cash":-1}})
        # A card-only offer can omit both deed arrays.
        incoming["query"] = {"kind":"accept","give_cards":["chance"],"cash":-25}
        self.assertEqual(native.evaluate_trade(self.strategy,incoming)["cash_after"],[1525,1475])

    def test_declining_packages_throttles_proposals_and_acceptance_reopens_them(self):
        current = example("offer")
        current["capabilities"] = [CAPABILITY]
        def observed(kind, turn):
            return dict(kind=kind, turn=turn, amount=40, generosity=90)
        history = [observed("declined_bundle",turn) for turn in range(1,9)]
        current["state"]["behavior"] = [[],history]
        self.assertGreater(native.scout(self.strategy,current)["refusal_probability"],.8)
        self.assertEqual(native.choose(self.strategy,current)["action"]["value"],0)
        # At the scheduled probe turn the bot must still be able to discover a change.
        current["state"]["turn"] = 32
        self.assertEqual(native.choose(self.strategy,current)["action"]["value"],1)
        current["state"]["turn"] = 29
        history.extend(observed("accepted_bundle",turn) for turn in range(9,17))
        self.assertLess(native.scout(self.strategy,current)["refusal_probability"],.4)
        self.assertEqual(native.choose(self.strategy,current)["action"]["value"],1)
        # The "don't propose" gate never suppresses evaluation of a new incoming offer.
        incoming = packet([16,18,27],[])
        incoming["state"]["behavior"][0] = history[:8]
        self.assertEqual(native.choose(self.strategy,incoming)["action"]["value"],1)

    def test_competition_executable_generates_and_receives_multi_deed_packages(self):
        outgoing = example("offer")
        outgoing.update(capabilities=[CAPABILITY],game_id="room",request_id="opening")
        def run(mode,msg):
            proc = subprocess.run([str(native.ROOT / "monopoly"),mode],input=json.dumps(msg)+"\n",
                                  capture_output=True,text=True,check=True)
            return json.loads(proc.stdout)
        response = run("bot",outgoing)
        action = response["action"]
        self.assertGreaterEqual(max(len(action["give_deeds"]),len(action["take_deeds"])),2)
        self.assertEqual((response["game_id"],response["request_id"]),("room","opening"))
        incoming = {**outgoing,"player":1,"request_id":"recipient","query":{**action,"kind":"accept"}}
        received, explained = run("bot",incoming), run("assess",incoming)
        self.assertEqual(received["request_id"],"recipient")
        self.assertEqual(received["action"]["value"] == 1,explained["decision"] == "accept")
        self.assertEqual(explained["acceptance_method"],"bundle_asset_heuristic")
        self.assertGreater(explained["proposer"]["value_after"],explained["proposer"]["value_before"])


if __name__ == "__main__":
    unittest.main()
