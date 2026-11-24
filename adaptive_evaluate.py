"""Seat-balanced paired comparison of the adaptive entrant and frozen neural model."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
from challenger_train import train_challenger

ROOT = Path(__file__).resolve().parent
STAKES = ("collector", "builder", "cautious", "legacy", "blocker", "frozen_neural",
          "trading_then_refusal", "refusal_then_trading", "reactive", "independent_bidder", "learned_rival")
KINDS = ("buy", "bid", "manage", "liquidate", "jail", "offer", "accept")
OUTCOMES = ("win", "loss", "cap")
PRIOR = ("acquisition", "completion", "denial", "railroads", "auction", "reserve",
         "development", "financing", "trade_demand", "trade_defense", "jail_patience", "house_scarcity")
V2_FIXTURES = ("incumbent_v1", "prior_challenger")
WIDTH = 25


def stream(seed, opponent, role):
    key = f"monopoly-adaptive-comparison-v1/{seed}/{opponent}/{role}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "little")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def engine_digest():
    h = hashlib.sha256()
    units = "board economy policy holdout search neural_value neural_policy neural_trade response opponent adaptive_policy adaptive_bench"
    if (ROOT/"src"/"resilience.cpp").is_file():
        units += " resilience"
    paths = sorted((ROOT/"src").glob("*.hpp")) + [ROOT/"src"/(name+".cpp") for name in units.split()]
    for path in paths:
        h.update(path.name.encode()+b"\0"+path.read_bytes())
    return h.hexdigest()


def check_model(path, expected):
    if digest(path) != expected:
        raise ValueError(f"Model checksum mismatch for {path.name}")
    return str(path)


def model_inputs(path):
    manifest=json.loads(path.read_bytes())
    if manifest["format"] not in ("monopoly.adaptive.v1", "monopoly.adaptive.v2"):
        raise ValueError("Expected an adaptive model manifest")
    folder=path.parent
    basepath=Path(check_model(folder/manifest["base"],manifest["base_sha256"]))
    base=json.loads(basepath.read_bytes())
    files=[check_model(basepath.parent/base["value_network"],base["sha256"]),
           check_model(basepath.parent/base["response_network"],base["response_sha256"]),
           check_model(folder/manifest["opponent"],manifest["opponent_sha256"])]
    specialist=json.loads(Path(check_model(folder/manifest["specialist"],manifest["specialist_sha256"])).read_bytes())["weights"]
    if len(specialist)!=20: raise ValueError("Expected twenty specialist weights")
    w=[base["prior_weights"][k] for k in PRIOR]+list(specialist)
    if not all(type(x) in (float,int) and 0<=x<=1 for x in w): raise ValueError("Invalid adaptive weights")
    serialized=" ".join(map(str,w))
    if manifest["format"]=="monopoly.adaptive.v2":
        knobs=json.loads(Path(check_model(folder/manifest["resilience"],manifest["resilience_sha256"])).read_bytes())
        r=knobs["weights"]
        bounds=((0,1),(0,1),(0,1),(.5,2),(.5,2),(-150,150),(0,1),(0,1))
        if (knobs.get("format")!="monopoly.resilience.v1" or len(r)!=len(bounds) or
                not all(type(x) in (float,int) and lo<=x<=hi for x,(lo,hi) in zip(r,bounds))):
            raise ValueError("Invalid resilience weights")
        serialized+=" v2 "+" ".join(map(str,r))
    return manifest,files,serialized


def serialized_rival(checkpoint):
    w=checkpoint["weights"]
    if checkpoint.get("format")!="monopoly.fixture.challenger.v1" or len(w)!=6 or not all(type(x) in (float,int) and 0<=x<=1 for x in w):
        raise ValueError("Invalid challenger checkpoint")
    return " ".join(map(str,w))


def read_known_rival(path, report_path, cap):
    rival=json.loads(path.read_bytes())
    report=json.loads(report_path.read_bytes())
    declared=report["rivals"]["learned_rival"] if "rivals" in report else report["trained_challenger"]
    if (declared["checkpoint_sha256"]!=digest(path) or
        report["manifest_sha256"]!=rival["target_manifest_sha256"] or
        report["engine_sha256"]!=rival["engine_sha256"] or rival["turn_cap"]!=cap):
        raise ValueError("Archived challenger does not match its original qualification record")
    serialized_rival(rival)
    return rival


def run_chunk(opponent, start, count, args, files, weights):
    cmd = [str(ROOT / "build" / "adaptive_bench"), "learned_rival" if opponent=="prior_challenger" else opponent,
           str(stream(args.seed, opponent, "board")), str(start), str(count), str(args.cap),
           *files, str(stream(args.seed, opponent, "regime"))]
    lines = subprocess.run(cmd, input=weights, text=True, capture_output=True)
    if lines.returncode:
        raise RuntimeError(f"{opponent} failed on pair {start}: {lines.stderr}")
    rows = []
    for line in lines.stdout.splitlines():
        x = [int(n) for n in line.split()]
        if len(x) != 2 + 2*WIDTH:
            raise ValueError("Malformed native result")
        rows.append((x[0], x[1], x[2:2+WIDTH], x[2+WIDTH:]))
    if [(i, seat) for i, seat, *_ in rows] != [(i, p) for i in range(start, start+count) for p in (0, 1)]:
        raise ValueError("Missing or out-of-order seat pair")
    return opponent, rows


def interval(estimates, cap=1):
    mean = statistics.mean(estimates)
    if len(estimates) == 1:
        return {"estimate": mean, "interval_95": None}
    radius = 1.959963984540054 * statistics.stdev(estimates) / math.sqrt(len(estimates))
    return {"estimate": mean, "interval_95": [max(-cap, mean-radius), min(cap, mean+radius)]}


def summarize(rows):
    rows = sorted(rows)
    n = len(rows)
    stats = []
    rates = []
    for index in (2, 3):
        sums = [sum(row[index][col] for row in rows) for col in range(WIDTH)]
        outcomes = Counter(row[index][0] for row in rows)
        if any(key not in (0, 1, 2) for key in outcomes):
            raise ValueError("Unexpected game result")
        per_pair = [(int(rows[i][index][0] == 0) + int(rows[i+1][index][0] == 0)) / 2
                    for i in range(0, n, 2)]
        rates.append(per_pair)
        conf = interval(per_pair)
        if conf["interval_95"]:
            conf["interval_95"][0] = max(0, conf["interval_95"][0])
        stats.append(dict(games=n, wins=outcomes[0], losses=outcomes[1], caps=outcomes[2],
                          win_rate=conf, total_turns=sums[1],
                          wins_by_seat=[sum(row[1] == p and row[index][0] == 0 for row in rows) for p in (0, 1)],
                          offers_sent=sums[2], outgoing_accepted=sums[3],
                          offers_received=sums[4], incoming_accepted=sums[5],
                          outbound_acceptance_rate=sums[3]/sums[2] if sums[2] else None,
                          inbound_acceptance_rate=sums[5]/sums[4] if sums[4] else None,
                          fixture=dict(games_observing_a_switch=sum(row[index][6] > 0 for row in rows),
                                       total_transitions=sums[6], decisions_open=sums[7], decisions_closed=sums[8],
                                       trade_actions_when_open=sums[9], trade_actions_suppressed_when_closed=sums[10])))
    total = [sum(r[3][i] for r in rows) for i in range(WIDTH)]
    by_kind = {k: {"checked": total[11+i], "changed": total[18+i]} for i, k in enumerate(KINDS)}
    cross = {a: {b: 0 for b in OUTCOMES} for a in OUTCOMES}
    for _, _, old, new in rows:
        cross[OUTCOMES[old[0]]][OUTCOMES[new[0]]] += 1
    return {"frozen": stats[0], "adaptive": stats[1],
            "win_rate_change": interval([b-a for a, b in zip(*rates)]),
            "outcomes_frozen_to_adaptive": cross, "rival_history_ablation_on_actual_decisions": by_kind}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=2000, help="Even number of games per entrant and opponent")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--cap", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--model", type=Path, default=ROOT/"models"/"adaptive-v1.json")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--opponents", nargs="+", choices=STAKES+V2_FIXTURES, default=None)
    parser.add_argument("--rival-seed", type=int, default=None)
    parser.add_argument("--rival-checkpoint", type=Path, default=None,
                        help="Train and save the challenger here, or reuse a matching trained checkpoint")
    parser.add_argument("--prior-challenger", type=Path, default=ROOT/"reports"/"qualification-rival.json",
                        help="Keep the known challenger trained against adaptive v1 fixed")
    parser.add_argument("--prior-report", type=Path, default=ROOT/"reports"/"adaptive-evaluation.json",
                        help="Original v1 report attesting to the known challenger's training provenance")
    args = parser.parse_args()
    if args.games < 2 or args.games % 2 or args.workers < 1 or args.cap < 1:
        parser.error("Use positive cap/workers and an even game count of at least 2")
    subprocess.run(["make", "build/adaptive_bench"], cwd=ROOT, check=True)
    executable_hash=digest(ROOT/"build"/"adaptive_bench")
    input_manifest_hash=digest(args.model)
    manifest, files, weights = model_inputs(args.model)
    v2 = manifest["format"] == "monopoly.adaptive.v2"
    args.opponents = args.opponents or list(STAKES + (V2_FIXTURES if v2 else ()))
    if len(set(args.opponents)) != len(args.opponents):
        parser.error("Opponent names must be unique")
    args.rival_seed = args.rival_seed if args.rival_seed is not None else (64713921 if v2 else 63289011)
    args.rival_checkpoint = args.rival_checkpoint or ROOT/"reports"/("qualification-rival-v2.json" if v2 else "qualification-rival.json")
    args.report = args.report or ROOT/"reports"/("adaptive-v2-evaluation.json" if v2 else "adaptive-evaluation.json")
    fixture_weights = {o: weights for o in args.opponents}
    known = None
    if "prior_challenger" in args.opponents:
        known = read_known_rival(args.prior_challenger, args.prior_report, args.cap)
        if args.seed in (known["seed"], args.rival_seed) or args.rival_checkpoint.resolve()==args.prior_challenger.resolve() or args.rival_seed==known["seed"]:
            raise ValueError("Use distinct final and training seeds and separate challenger checkpoints")
        fixture_weights["prior_challenger"] += " " + serialized_rival(known)
    rival = None
    build_hash = engine_digest()
    if "learned_rival" in args.opponents:
        if args.seed == args.rival_seed:
            raise ValueError("Use distinct training and qualification seeds")
        if args.rival_checkpoint.exists():
            rival = json.loads(args.rival_checkpoint.read_text())
            archived = False
            if not v2 and rival.get("engine_sha256") != build_hash and args.prior_report.is_file():
                attested = read_known_rival(args.rival_checkpoint, args.prior_report, args.cap)
                archived = attested["target_manifest_sha256"] == digest(args.model)
            if (rival["target_manifest_sha256"] != digest(args.model) or (rival.get("engine_sha256") != build_hash and not archived)
                    or rival["seed"] != args.rival_seed
                    or rival["turn_cap"] != args.cap):
                raise ValueError("Rival checkpoint targets a different entrant, seed or cap. Supply a new --rival-checkpoint path.")
        else:
            rival = train_challenger(ROOT/"build"/"adaptive_bench", files, weights, args.cap,
                                     args.workers, args.rival_seed, digest(args.model), build_hash, args.rival_checkpoint)
        fixture_weights["learned_rival"] += " " + serialized_rival(rival)
    by_opponent = {o: [] for o in args.opponents}
    pairs = args.games // 2
    # One job per 25 pairs. Shard boundaries and process ordering do not change the seeds.
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(run_chunk, o, i, min(25, pairs-i), args, files, fixture_weights[o])
                   for i in range(0, pairs, 25) for o in args.opponents]
        for future in as_completed(futures):
            o, rows = future.result()
            by_opponent[o].extend(rows)
    results = {o: summarize(rows) for o, rows in by_opponent.items()}
    mirror_rival = "incumbent_v1" if v2 else "frozen_neural"
    if mirror_rival in results:
        mirror = results[mirror_rival]["frozen"]
        if mirror["wins"] != mirror["losses"]:
            raise RuntimeError("Seat-swapped frozen mirror failed its symmetry check")
    trial = subprocess.run([str(ROOT/"build"/"adaptive_bench"), "witness", "1", "0", "1", "1000",
                            *files, "1"], input=weights, text=True, capture_output=True, check=True)
    data = trial.stdout.split()
    histories = ("empty", "eight_favorable_deals_accepted", "eight_favorable_deals_declined",
                 "eight_refusals_then_eight_acceptances")
    witness = {"synthetic": True, "seat": 0, "turn": 52, "board": "unchanged across cases",
               "auction_square": int(data[0]), "cases": {
                   name: {"bid": int(data[1+2*i]), "estimated_refusal": float(data[2+2*i])}
                   for i, name in enumerate(histories)}}
    if (engine_digest()!=build_hash or digest(ROOT/"build"/"adaptive_bench")!=executable_hash
            or digest(args.model)!=input_manifest_hash):
        raise RuntimeError("Qualification inputs changed during evaluation. Rerun from the frozen build.")
    report = dict(baseline="incumbent adaptive v1 with the same base, specialist and opponent weights" if v2 else "frozen neural v1",
                  entrant=manifest["format"], seed=args.seed, games_per_entrant_and_opponent=args.games, turn_cap=args.cap,
                  method="Each seed plays both seats against each entrant, with identical seed and fixture conditions. "
                         "A game reaching the turn cap counts as a non-win. Intervals use normal approximations "
                         "to the standard error across independent seed pairs; changes use within-pair differences.",
                  actual_decision_ablation="For both games of every 50th seed, evaluate each adaptive decision twice. "
                        "The second evaluation clears only the opposing seat's behavior events. The observed-history action is played.",
                  switch_fixture="Trading starts with the cautious profile. During refusal, proposals/agreements stop and "
                        "auctions use the independent bidder. The direction switches once on a hidden, uniformly sampled "
                        "individual turn 18–53. In the reactive fixture two personally unacceptable received offers "
                        "trigger a hidden 8–22 turn cooldown; accepting resets the counter.",
                  manifest_sha256=input_manifest_hash, engine_sha256=build_hash, harness_sha256=digest(ROOT/"src"/"adaptive_bench.cpp"),
                  executable_sha256=executable_hash,
                  same_position_history_reversal=witness, results=results)
    if known:
        report["previously_known_challenger"] = {k: known[k] for k in ("seed","training_games","weights","target_manifest_sha256","engine_sha256")}
        report["previously_known_challenger"]["checkpoint_sha256"] = digest(args.prior_challenger)
        report["previously_known_challenger"]["report_sha256"] = digest(args.prior_report)
    if rival:
        report["trained_challenger"] = {k: rival[k] for k in ("seed","training_games","parameter_names","weights","scope")}
        report["trained_challenger"]["checkpoint_sha256"] = digest(args.rival_checkpoint)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temp = args.report.with_suffix(".pending")
    temp.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    temp.replace(args.report)
    for name, r in results.items():
        old, new = r["frozen"]["win_rate"]["estimate"], r["adaptive"]["win_rate"]["estimate"]
        print(f"{name:24} fixed {old:6.2%}  adaptive {new:6.2%}  delta {new-old:+.2%}")


if __name__ == "__main__":
    main()
