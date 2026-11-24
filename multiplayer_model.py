"""Checkpoint contract for a seat-count-specific neural policy over frozen adaptive v4."""
import hashlib
import json
import math
from pathlib import Path

import native

FORMAT = "monopoly.multiplayer.v1"
ARCHITECTURE = "full-table-residual-v1"
PARAMS = 24
BASE = "adaptive-v4"
BASE_PATH = native.ROOT / "models" / (BASE + ".json")


def _weights(values):
    if not isinstance(values, (list, tuple)) or len(values) != PARAMS:
        raise ValueError(f"The multiplayer network needs {PARAMS} weights")
    if any(type(x) not in (float, int) or not -4 <= x <= 4 or not math.isfinite(x) for x in values):
        raise ValueError("Multiplayer network weights must be finite and between -4 and 4")
    return [float(x) for x in values]


def candidate(seats, weights):
    if type(seats) is not int or seats not in (3, 4):
        raise ValueError("Multiplayer checkpoints require three or four seats")
    return {
        "format": FORMAT, "architecture": ARCHITECTURE, "seats": seats,
        "base": BASE, "base_sha256": hashlib.sha256(BASE_PATH.read_bytes()).hexdigest(),
        "weights": _weights(weights),
    }


def load(name, seats):
    if type(seats) is not int or seats not in (3, 4):
        raise ValueError("Expected three or four players")
    if name in (None, "champion", "multiplayer"):
        path = native.ROOT / "models" / f"multiplayer-{seats}.json"
        if not path.is_file():
            return native.model(BASE), None
    elif name in ("multiplayer-3", "multiplayer-4"):
        path = native.ROOT / "models" / (name + ".json")
    else:
        path = Path(name)
        if not path.is_file():
            return native.model(name), None
    doc = json.loads(path.read_bytes())
    if doc.get("format") != FORMAT:
        return native.model(str(path)), None
    if type(doc.get("seats")) is not int or doc["seats"] != seats:
        raise ValueError(f"{path} was not trained for {seats} seats")
    if doc.get("architecture") != ARCHITECTURE or doc.get("base") != BASE:
        raise ValueError(f"Incompatible multiplayer network in {path}")
    if doc.get("base_sha256") != hashlib.sha256(BASE_PATH.read_bytes()).hexdigest():
        raise ValueError(f"The base neural model for {path} failed checksum verification")
    return native.model(BASE), _weights(doc.get("weights"))
