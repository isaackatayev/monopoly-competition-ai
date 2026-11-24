import ctypes as C
import unittest

import four_player as four
import native


class FourPlayerBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = four.load_engine()
        cls.names = ["collector"] * 3
        cls.configs = [native.config(native.model("collector")) for _ in range(4)]

    def test_identical_players_rotate_through_every_seat(self):
        for index in range(6):
            stats = four.play_board(self.engine, self.configs, self.names, index,
                                    four.board_seed(832716409, index), 1000, True)
            self.assertEqual((stats["wins"], stats["losses"], stats["caps"]), (1, 3, 0))
            self.assertEqual(stats["winner_collector"], 3)
            self.assertEqual(stats["outgoing_accepted"], stats["incoming_accepted"])
            self.assertEqual(stats["between_opponents_accepted"], 2 * stats["outgoing_accepted"])

    def test_cap_and_disabled_trading(self):
        stats = four.play_board(self.engine, self.configs, self.names, 0, 79, 1, False)
        self.assertEqual((stats["wins"], stats["losses"], stats["caps"], stats["turns"]),
                         (0, 0, 4, 4))
        self.assertEqual(sum(stats[k] for k in stats if k.endswith("_proposed")), 0)

    def test_three_identical_players_rotate_with_two_opponents(self):
        for index in range(4):
            stats = four.play_board(self.engine, self.configs[:3], self.names[:2], index,
                                    four.board_seed(832716409, index, 3), 1000, True)
            self.assertEqual(stats["wins"] + stats["losses"] + stats["caps"], 3)
            self.assertIn((stats["wins"], stats["losses"], stats["caps"]), ((1, 2, 0), (0, 0, 3)))
            self.assertEqual(stats["between_opponents_accepted"], stats["outgoing_accepted"])
            self.assertEqual(stats["incoming_accepted"], stats["outgoing_accepted"])

    def test_zero_weights_preserve_decisions_and_invalid_weights_fail(self):
        args = (self.engine, self.configs, self.names, 1, 8237, 1000, True)
        self.assertEqual(four.play_board(*args), four.play_board(*args, weights=[[0.] * 24] + [None] * 3))
        with self.assertRaises(ValueError):
            four.play_board(*args, weights=[[float("nan")] * 24] + [None] * 3)

    def test_weights_rotate_with_their_owner_in_both_formats(self):
        class CheckTable:
            def multiplayer_play(self, table, seats, seed, cap, trading, weights, count, out, *_):
                for i in range(seats):
                    self_test.assertEqual(weights[i][0], table[i].style)
                result = C.cast(out, C.POINTER(four.Result)).contents
                result.winner, result.survivors, result.turns = 0, 1, 8
                return 1

        self_test = self
        names = ["collector", "builder", "cautious", "blocker"]
        configs = [native.config(native.model(name)) for name in names]
        for seats in (3, 4):
            weights = [[float(c.style)] * 24 for c in configs[:seats]]
            for index in range(len(four.OPPONENT_ORDERS[seats])):
                stats = four.play_board(CheckTable(), configs[:seats], names[1:seats],
                                        index, 793, 1000, True, weights)
                self.assertEqual((stats["wins"], stats["losses"]), (1, seats - 1))
