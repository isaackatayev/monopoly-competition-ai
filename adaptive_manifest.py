"""Package trained opponent, auction, and board models for the competition client."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


def write_manifest(directory, bidding_model=False, name="adaptive.json", league=False, deal_model=False, pressure_mode=None):
    if pressure_mode is not None:
        if pressure_mode not in ("pressure","observed"):
            raise ValueError("Unknown acquisition mode")
        league=deal_model=True
    manifest = {"format": "monopoly.adaptive.v1", "base": "neural.json",
                "opponent": "opponent.bin", "specialist": "specialist.json"}
    assets = ["base", "opponent", "specialist"]
    if bidding_model or league:
        manifest.update(format="monopoly.adaptive.v2", resilience="resilience.json")
        assets.append("resilience")
    if league:
        manifest.update(format="monopoly.adaptive.v3",robust="robust.json")
        assets.append("robust")
        if deal_model:
            manifest["deal_value"]="deal.bin"
            assets.append("deal_value")
        if pressure_mode:
            manifest.update(format="monopoly.adaptive.v4",pressure="pressure.json",acquisition_mode=pressure_mode)
            assets.append("pressure")
    for asset in assets:
        manifest[asset+"_sha256"] = hashlib.sha256((directory/manifest[asset]).read_bytes()).hexdigest()
    from native import model
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=directory, delete=False) as out:
        stage = Path(out.name)
        json.dump(manifest, out, indent=2)
        out.write("\n")
    try:
        model(stage)
        os.replace(stage, directory/name)
    finally:
        stage.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent/"models")
    parser.add_argument("--bidding-model", action="store_true", help="Include trained rival bidding forecasts")
    parser.add_argument("--league", action="store_true", help="Package the league player")
    parser.add_argument("--deal-model", action="store_true", help="Package league trade-delta weights")
    parser.add_argument("--pressure-mode",choices=("observed","pressure"),help="Select v4 auction routing")
    parser.add_argument("--name",default="adaptive.json",help="Output manifest filename")
    args = parser.parse_args()
    if Path(args.name).name!=args.name:
        parser.error("Use a filename within the model directory")
    active=args.directory/args.name
    if active.exists() and not (args.bidding_model or args.league or args.deal_model or args.pressure_mode):
        current=json.loads(active.read_bytes())
        args.pressure_mode=current.get("acquisition_mode")
        args.league=current["format"]=="monopoly.adaptive.v3"
        args.deal_model="deal_value" in current
        args.bidding_model=current["format"]=="monopoly.adaptive.v2"
    write_manifest(args.directory,args.bidding_model,name=args.name,league=args.league or args.deal_model,
                   deal_model=args.deal_model,pressure_mode=args.pressure_mode)
