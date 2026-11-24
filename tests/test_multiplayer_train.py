import random
import unittest

import multiplayer_train as train


class MultiplayerTrainingTests(unittest.TestCase):
    def test_search_and_selection_use_disjoint_seeds(self):
        for size in (3, 4):
            def plans(phase, stage):
                return train.schedule(size, 101, phase, stage, 24, train.ZERO)

            train_keys = {b.seed for b in plans("search", 0)}
            self.assertEqual(len(train_keys), 24)
            self.assertFalse(train_keys.intersection(b.seed for b in plans("validation", 0)))
            self.assertFalse(train_keys.intersection(b.seed for b in plans("search", 1)))
            for board in plans("search", 0):
                self.assertEqual(len(board.rivals), size - 1)
                self.assertEqual(len(board.weights), size - 1)
            self.assertEqual([b.seed for b in plans("search", 0)], [b.seed for b in plans("search", 0)])
            for i in range(len(train.ROSTERS[size])):
                self.assertEqual(sum(b.fixture == i for b in plans("search", 0)), 4)

    def test_training_mutates_all_network_parameters(self):
        first = train.population(16, 0, [], random.Random(7))
        second = train.population(16, 1, [first[1], first[3], first[9]], random.Random(8))
        self.assertEqual(first[0], train.ZERO)
        self.assertIn(first[1], second)
        self.assertEqual(len(set(second)), 16)
        self.assertTrue(all(-4 <= w <= 4 for vector in second for w in vector))
        self.assertTrue(all(len(set(dim)) > 1 for dim in zip(*second)))
        for slate in (first, second):
            bid_only = [v for v in slate if any(v[:8]) and not any(v[8:16])]
            trade_only = [v for v in slate if any(v[8:16]) and not any(v[:8])]
            self.assertTrue(min(v[0] for v in bid_only) < 0 < max(v[0] for v in bid_only))
            self.assertTrue(min(v[8] for v in trade_only) < 0 < max(v[8] for v in trade_only))
            self.assertTrue(any(any(v[:8]) and any(v[8:16]) for v in slate))

    def test_evaluation_schedule_rotates_order_within_each_fixture(self):
        plans = train.schedule(4, 24, "search", 0, 36, train.STARTS[0])
        for fixture in range(6):
            group = [p for p in plans if p.fixture == fixture]
            self.assertEqual({p.index % 6 for p in group}, set(range(6)))

    def test_incumbent_is_a_separate_frozen_opponent(self):
        for size in (3, 4):
            for board in train.schedule(size, 101, "search", 0, 6, train.STARTS[0]):
                for index, entry in enumerate(train.ROSTERS[size][board.fixture]):
                    if entry == "@incumbent":
                        self.assertEqual(board.rivals[index], train.BASE)
                        self.assertEqual(board.weights[index], train.STARTS[0])
