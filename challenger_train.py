"""Train a bounded challenger against a locked adaptive entrant, then export its weights."""
import hashlib
import json
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RIVAL_PARAMS = ["blocker_to_neural_bid_mix", "bid_premium", "denial_premium", "cash_reserve",
                "negotiation_mode", "asset_management_mode"]


def independent_stream(seed, label):
    return int.from_bytes(hashlib.sha256(f"challenger-only-v1/{seed}/{label}".encode()).digest()[:8], "little")


def train_challenger(executable, files, model_weights, cap, workers, seed, entrant_hash, engine_hash, destination,
                     entrant_label="style 7"):
    # Candidate training sees only completed games. The challenger has no access
    # to the opponent's private memory, bids, weights, dice or shuffled decks.
    def score(candidate, phase, games):
        input_weights = model_weights + " " + " ".join(map(str, candidate))
        cmd = [str(executable), "rival_train", str(independent_stream(seed, phase)), "0",
               str(games//2), str(cap), *files, "0"]
        p = subprocess.run(cmd, input=input_weights, text=True, capture_output=True, check=True)
        w, l, c = map(int, p.stdout.split())
        if w+l+c != games:
            raise RuntimeError("Incomplete challenger training batch")
        return {"weights": candidate, "wins": w, "losses": l, "caps": c}

    rng = random.Random(seed)
    vectors = [[0, .5, .5, 0, 0, 0], [1, .5, .5, 0, 1, 1], [.4, .7, .8, .2, 0, 1]]
    vectors += [[rng.random() for _ in RIVAL_PARAMS] for _ in range(8-len(vectors))]
    phases = []
    finalists = []
    with ThreadPoolExecutor(workers) as pool:
        for generation in range(3):
            label = f"search-{generation}"
            runs = list(pool.map(lambda x: score(x, label, 120), vectors))
            # Optimize outright opponent wins. Fewer unresolved games breaks ties.
            ranked = sorted(runs, key=lambda r: (-r["wins"], r["caps"]))
            elites = [r["weights"] for r in ranked[:2]]
            for e in elites:
                if e not in finalists:
                    finalists.append(e)
            phases.append({"phase": label, "games_per_candidate": 120, "candidates": runs})
            spread = .24 if generation == 0 else .15
            vectors = elites + [[max(0, min(1, rng.gauss(w, spread))) for w in elites[i%2]]
                                for i in range(6)]
        candidates = finalists[-4:]
        if len(candidates) < 4:
            for r in ranked:
                if r["weights"] not in candidates:
                    candidates.append(r["weights"])
                if len(candidates) == 4:
                    break
        final = list(pool.map(lambda x: score(x, "selection", 384), candidates))
    selected = sorted(final, key=lambda r: (-r["wins"], r["caps"]))[0]["weights"]
    phases.append({"phase": "selection", "games_per_candidate": 384, "candidates": final})
    count = sum(len(p["candidates"])*p["games_per_candidate"] for p in phases)
    report = {"format": "monopoly.fixture.challenger.v1", "parameter_names": RIVAL_PARAMS,
              "weights": selected, "seed": seed, "training_games": count, "turn_cap": cap,
              "target_manifest_sha256": entrant_hash, "engine_sha256": engine_hash,
              "method": f"Three eight-candidate evolutionary rounds against locked {entrant_label} use common "
                        "seat-swapped seeds within each round. Up to four elites compete on fresh "
                        "selection seeds. Higher outright challenger wins select the fixed vector.",
              "scope": "Six parameters blend frozen neural and blocker deed ceilings, scale bids and "
                       "denial, reserve cash, choose refusal/accept-only/full negotiation, and choose "
                       "neural or blocker asset management. No updates during the final evaluation.",
              "phases": phases}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".pending")
    temp.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    temp.replace(destination)
    return report
