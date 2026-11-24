import copy
import unittest

from arena import example
from live_player import LivePlayer
from native import evaluate_trade, model, parse_request, scout


def event(kind, turn, generosity=250):
    return dict(kind=kind, turn=turn, square=27, give=27, take=19, amount=100,
                generosity=generosity)


class AdaptiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = model("adaptive")

    def test_deployed_default_and_candidate_are_distinct(self):
        self.assertEqual(model("champion")[0],11)
        self.assertEqual(model("adaptive-v1")[0],7)
        self.assertEqual(model("adaptive-v2")[0],8)
        self.assertEqual(model("adaptive-v3")[0],9)
        self.assertEqual(model("pressure")[0],10)
        self.assertEqual(model("frozen")[0],6)
        for entrant in (self.bot,model("adaptive-v2")):
            player = LivePlayer(entrant)
            for scenario in ("offer","trade"):
                packet = example(scenario)
                answer = player.choose(packet)
                self.assertEqual(answer["request_id"],packet["request_id"])

    def test_model_revises_refusal_and_reprices_offer(self):
        packet = example("trade")
        # The recipient of our hypothetical proposal is seat 1.
        packet["player"] = 0
        packet["query"] = {"kind":"offer"}
        cold = scout(self.bot, packet)["refusal_probability"]
        packet["state"]["behavior"][1] = [event("declined_trade",i) for i in range(1,9)]
        closed = scout(self.bot, packet)["refusal_probability"]
        reverse = [event("accepted_trade", i) for i in range(9,17)]
        packet["state"]["behavior"][1] += reverse
        reopened = scout(self.bot, packet)["refusal_probability"]
        self.assertEqual(cold, .5)
        self.assertGreater(closed, .85)
        self.assertLess(reopened, .2)
        deal = example("trade")
        base = evaluate_trade(self.bot, deal)
        deal["state"]["behavior"][1] = [event("declined_trade",i) for i in range(1,9)]
        self.assertLess(evaluate_trade(self.bot,deal)["estimated_acceptance"],base["estimated_acceptance"])

    def test_trade_forecast_responds_to_rival_history(self):
        entrant = model("adaptive-v3")
        packet = example("trade")
        cold = evaluate_trade(entrant, packet)
        self.assertFalse(cold["used_deal_model"])
        packet["state"]["behavior"][0] = [event("declined_trade",t) for t in range(3)]
        informed = evaluate_trade(entrant, packet)
        self.assertTrue(informed["used_deal_model"])
        self.assertEqual(informed["predicted_recipient_gain_if_accepted"],
                         cold["predicted_recipient_gain_if_accepted"])
        self.assertLessEqual(abs(informed["predicted_recipient_gain_if_accepted"]), 1)
        self.assertNotIn("used_deal_model", evaluate_trade(model("adaptive-v1"), packet))

    def test_tracker_replay_sparse_and_cross_game(self):
        player = LivePlayer(self.bot)
        first = example("offer")
        del first["state"]["behavior"]
        first["opponent_id"] = "visitor"
        reply = player.choose(first)
        self.assertEqual(reply, player.choose(copy.deepcopy(first)))
        terms = reply["action"]
        self.assertGreaterEqual(max(terms["give"],terms["take"]),0)
        second = copy.deepcopy(first)
        second["request_id"] = 2
        second["query"] = {"kind":"manage"}
        second["state"]["last_rejected"] = [{k: terms[k] for k in ("give","take","cash")},None]
        second["state"]["last_rejected"][0]["turn"] = first["state"]["turn"]
        player.choose(second)
        self.assertEqual(len(player.games[("demo",0)].history[1]),1)
        player.choose(second)
        self.assertEqual(len(player.games[("demo",0)].history[1]),1)
        rematch = example("trade")
        rematch.update(game_id="return",opponent_id="visitor",previous_game_id="demo")
        player.choose(rematch)
        self.assertEqual(player.games[("return",1)].prior[0]["kind"],"declined_trade")
        rematch.update(game_id="unrelated",opponent_id="different")
        player.choose(rematch)
        self.assertEqual(player.games[("unrelated",1)].prior,[])
        changed = copy.deepcopy(first)
        changed["state"]["turn"] += 1
        with self.assertRaises(ValueError):
            player.choose(changed)

    def test_snapshot_only_rejection(self):
        player = LivePlayer(self.bot)
        first = example("offer")
        del first["state"]["behavior"]
        action = player.choose(first)["action"]
        self.assertGreaterEqual(max(action["give"],action["take"]),0)
        next_decision = {**first, "request_id":2, "query":{"kind":"manage"}}
        player.choose(next_decision)
        self.assertEqual([e["kind"] for e in player.games[("demo",0)].history[1]],["declined_trade"])
        refusal = dict(give=action["give"],take=action["take"],cash=action["cash"],turn=first["state"]["turn"])
        third = {**next_decision,"request_id":3,
                 "state": {**next_decision["state"],"last_rejected":[refusal,None]}}
        player.choose(third)
        self.assertEqual(len(player.games[("demo",0)].history[1]),1)

    def test_host_can_omit_offer_valuation_and_absent_deed(self):
        player = LivePlayer(self.bot)
        packet = example("trade")
        packet["query"].update(give=-1,cash=500)
        observation = {"kind":"proposed_trade","take":19,"amount":500,"turn":29}
        packet["state"]["behavior"] = [[observation],[]]
        player.choose(packet)
        observed = player.games[("demo",1)].history[0][0]
        self.assertIn("generosity",observed)
        later = {**packet,"request_id":2,"query":{"kind":"manage"}}
        player.choose(later)
        self.assertEqual(player.games[("demo",1)].history[0][0]["generosity"],observed["generosity"])

    def test_history_validation(self):
        packet = example("offer")
        for events in ([event("declined_trade", 30)], [event("proposed_trade",0)]*17,
                       [event("accepted_trade",5),event("accepted_trade",4)]):
            packet["state"]["behavior"][1] = events
            with self.subTest(events=len(events)), self.assertRaises(ValueError):
                parse_request(packet)
