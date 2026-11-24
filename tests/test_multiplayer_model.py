import json
from pathlib import Path
import tempfile
import unittest

import multiplayer_model as model


class MultiplayerModelTests(unittest.TestCase):
    def test_roundtrip_validates_seat_count_and_frozen_base(self):
        weights = [.125] * model.PARAMS
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoint.json"
            doc = model.candidate(3, weights)
            path.write_text(json.dumps(doc))
            base, restored = model.load(path, 3)
            self.assertEqual((base[0], restored), (11, weights))
            with self.assertRaises(ValueError):
                model.load(path, 4)
            doc["base_sha256"] = "0" * 64
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                model.load(path, 3)

    def test_reject_bad_weights(self):
        for bad in ([], [0] * 23 + [float("nan")], [True] * 24, [5] * 24):
            with self.assertRaises(ValueError):
                model.candidate(4, bad)
