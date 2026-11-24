import ctypes as C
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native import Snapshot, snapshot_dict
from table_game import (TableError, TableGame, action_fields, legal_moves,
                        validate_action)


class LiveTableTests(unittest.TestCase):
    def wait_for_decision(self, game):
        with game.cv:
            self.assertTrue(game.cv.wait_for(lambda: game.view()["decision"] or game.status != "running", 4))
        return game.poll(0) or game.poll(1)

    def test_both_external_seats_are_asked_for_decisions(self):
        game = TableGame("match", 0, 15, ["North", "South"]).start()
        self.addCleanup(game.close)
        seats = set()
        while True:
            packet = self.wait_for_decision(game)
            if "status" in packet:
                break
            seat = packet["player"]
            seats.add(seat)
            self.assertIsNone(game.poll(1-seat))
            self.assertEqual(game.poll(seat), packet)
            action = packet["legal"]["options"][0]["action"]
            game.submit(seat, {**packet, "action": action})
        self.assertEqual(seats, {0, 1})
        self.assertEqual(game.view()["state"]["turn"], 15)
        self.assertEqual(packet["reason"], "turn_limit")
        self.assertTrue(any(owner >= 0 for owner in game.state["owner"]))

    def test_roll_authorship_retry_and_terminal_tie(self):
        game = TableGame("test", 8, 1, ["Human", "Other"], human_seats=[0]).start()
        self.addCleanup(game.close)
        packet = self.wait_for_decision(game)
        self.assertEqual(packet["query"], {"kind": "roll"})
        self.assertIsNone(game.poll(1))
        reply = {key: packet[key] for key in ("protocol", "game_id", "request_id")}
        reply["action"] = {"value": 0}
        with self.assertRaises(TableError) as wrong_seat:
            game.submit(1, reply)
        self.assertEqual(wrong_seat.exception.status, 409)
        with self.assertRaises(TableError) as illegal:
            game.submit(0, {**reply, "action": {"value": 1}})
        self.assertEqual(illegal.exception.status, 400)
        self.assertIsNone(game.view()["dice"])
        ack = game.submit(0, reply)
        self.assertEqual(ack, game.submit(0, reply))
        with self.assertRaises(TableError) as changed:
            game.submit(0, {**reply, "action": {"value": 1}})
        self.assertEqual(changed.exception.status, 409)
        game._thread.join(4)
        final = game.view()
        self.assertEqual(final["status"], "complete")
        self.assertEqual(final["reason"], "turn_limit")
        self.assertIsNone(final["winner"])
        self.assertEqual(final["state"]["turn"], 1)
        self.assertTrue(all(1 <= face <= 6 for face in final["dice"]))
        self.assertEqual(ack, game.submit(0, reply))

    def test_offer_includes_only_transferable_deeds_and_charges_interest(self):
        snapshot = Snapshot()
        snapshot.owner[:] = [-1] * 40
        snapshot.owner[1] = snapshot.owner[3] = snapshot.owner[5] = 0
        snapshot.owner[6] = 1
        snapshot.level[1] = 1
        snapshot.mortgaged[5] = 1
        snapshot.cash[:] = [500, 100]
        state = snapshot_dict(snapshot)
        query = {"kind": "offer"}
        legal = legal_moves(C.byref(snapshot), state, 0, query)
        packet = {"state": state, "query": query, "player": 0, "legal": legal}
        self.assertEqual(legal["trade"]["give"], [5])
        self.assertEqual(legal["trade"]["take"], [6])
        validate_action(action_fields({"give": 5, "cash": -90}), packet)
        with self.assertRaises(TableError):
            validate_action(action_fields({"give": 5, "cash": -91}), packet)


if __name__ == "__main__":
    unittest.main()
