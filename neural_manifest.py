"""Serialize the deployment inputs for the win-value player."""
import hashlib
import json
import os
from pathlib import Path


def write_manifest(model_path, legacy_path, manifest_path):
    weights, legacy, target = map(Path, (model_path, legacy_path, manifest_path))
    prior = json.loads(legacy.read_text())
    manifest = {
        "format": "monopoly.neural.v1",
        "value_network": os.path.relpath(weights, target.parent),
        "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
        "prior_weights": prior["weights"],
    }
    response = target.parent / "response.bin"
    if not response.exists():
        response = legacy.parent / "response.bin"
    if not response.is_file():
        raise ValueError("Train the response network before publishing a neural player")
    manifest["response_network"] = os.path.relpath(response, target.parent)
    manifest["response_sha256"] = hashlib.sha256(response.read_bytes()).hexdigest()
    pending = target.with_name(target.name + ".tmp")
    pending.write_text(json.dumps(manifest, indent=2) + "\n")
    pending.replace(target)
