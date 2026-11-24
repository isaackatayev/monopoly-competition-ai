"""Qualify a locked Monopoly entrant against archived, independent and newly trained rivals."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess

from adaptive_evaluate import (ROOT, WIDTH, check_model, digest, model_inputs, read_known_rival,
                               serialized_rival, summarize)
from challenger_train import train_challenger

BINARY = ROOT / "build" / "league_bench"
ARCHIVES = {"prior_challenger_v1": ("qualification-rival", "adaptive-evaluation"),
            "prior_challenger_v2": ("qualification-rival-v2", "adaptive-v2-evaluation"),
            "prior_challenger_v3": ("qualification-rival-v3", "league-v3")}
FIXTURES = ("prior_challenger_v1", "prior_challenger_v2", "known_additive", "learned_rival",
            "additive_liquidity", "additive_district", "blocker", "collector", "builder",
            "cautious", "legacy", "frozen_neural", "incumbent_v1", "trading_then_refusal",
            "refusal_then_trading", "reactive", "independent_bidder")
CODE = ("board", "economy", "policy", "holdout", "search", "neural_value", "neural_policy",
        "neural_trade", "response", "opponent", "adaptive_policy", "robust_policy",
        "adaptive_bench", "resilience_arena", "league_bench")


def engine_digest():
    h = hashlib.sha256()
    paths = sorted((ROOT/"src").glob("*.hpp")) + [ROOT/"src"/(n+".cpp") for n in CODE]
    # Include new translation units when the Makefile links them (for example a
    # tactical decision extension). The digest covers the sources behind CORE.
    make_vars = subprocess.run(["make", "-s", "--no-print-directory", "-f", "-", "core_list"],
                               input="include Makefile\ncore_list:\n\t@echo $(CORE)\n", cwd=ROOT,
                               capture_output=True, text=True, check=True)
    paths.extend(ROOT/p for p in make_vars.stdout.split())
    for path in sorted(set(paths)):
        h.update(path.relative_to(ROOT).as_posix().encode()+b"\0"+path.read_bytes())
    return h.hexdigest()


def entrant_inputs(incumbent_path, candidate_path):
    baseline, files, weights = model_inputs(incumbent_path)
    candidate = json.loads(candidate_path.read_bytes())
    if baseline["format"] != "monopoly.adaptive.v1":
        raise ValueError("The reference player must be adaptive v1")
    for component in ("base", "specialist", "opponent"):
        checksum = candidate[component+"_sha256"]
        if baseline[component+"_sha256"] != checksum:
            raise ValueError(f"The two entrants have different {component} checkpoints")
        check_model(candidate_path.parent/candidate[component], checksum)
    # Also verify network bytes relative to the candidate's own base manifest.
    base_path = candidate_path.parent/candidate["base"]
    base = json.loads(base_path.read_bytes())
    for key, checksum in (("value_network", "sha256"), ("response_network", "response_sha256")):
        check_model(base_path.parent/base[key], base[checksum])
    if candidate["format"] == "monopoly.adaptive.v1":
        return files, weights+" v1", candidate["format"]
    if candidate["format"] not in ("monopoly.adaptive.v3","monopoly.adaptive.v4"):
        raise ValueError("Expected a v3/v4 candidate or v1 reference replication")
    resilience_file = Path(check_model(candidate_path.parent/candidate["resilience"], candidate["resilience_sha256"]))
    resilience = json.loads(resilience_file.read_bytes())
    bounds = ((0,1),(0,1),(0,1),(.5,2),(.5,2),(-150,150),(0,1),(0,1))
    if (resilience.get("format") != "monopoly.resilience.v1" or len(resilience["weights"]) != len(bounds) or
            any(type(v) not in (float,int) or not lo <= v <= hi for v,(lo,hi) in zip(resilience["weights"],bounds))):
        raise ValueError("Invalid resilience checkpoint")
    if "deal_value" in candidate or "deal_value_sha256" in candidate:
        files.append(check_model(candidate_path.parent/candidate["deal_value"], candidate["deal_value_sha256"]))
    robust_file = Path(check_model(candidate_path.parent/candidate["robust"], candidate["robust_sha256"]))
    r = json.loads(robust_file.read_bytes())
    knobs = r["weights"]
    if (r["format"] != "monopoly.robust.v1" or len(knobs) != 12 or
            any(type(w) not in (float,int) or not 0 <= w <= 1 for w in knobs)):
        raise ValueError("Invalid twelve-weight robust acquisition checkpoint")
    version="v3"
    params=resilience["weights"]+knobs
    if candidate["format"]=="monopoly.adaptive.v4":
        from native import PRESSURE_BOUNDS
        pressure_file=Path(check_model(candidate_path.parent/candidate["pressure"],candidate["pressure_sha256"]))
        p=json.loads(pressure_file.read_bytes())
        if (p["format"]!="monopoly.pressure.v1" or len(p["weights"])!=len(PRESSURE_BOUNDS) or
                any(type(v) not in (int,float) or not lo<=v<=hi for v,(lo,hi) in zip(p["weights"],PRESSURE_BOUNDS))):
            raise ValueError("Invalid pressure weights")
        version={"observed":"v4","pressure":"pressure"}[candidate["acquisition_mode"]]
        params+=p["weights"]
    return files, weights+" "+version+" "+" ".join(map(str,params)), candidate["format"]


def stream(seed, fixture, purpose):
    key = f"monopoly-league-v3/{seed}/{fixture}/{purpose}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "little")


def chunk(fixture, first, pairs, args, files, weights):
    cmd = [str(BINARY), fixture, str(stream(args.seed, fixture, "board")), str(first), str(pairs),
           str(args.cap), *files, str(stream(args.seed, fixture, "conditions"))]
    process = subprocess.run(cmd, input=weights, text=True, capture_output=True)
    if process.returncode:
        raise RuntimeError(f"{fixture} pair {first}: {process.stderr}")
    rows = []
    for line in process.stdout.splitlines():
        x = list(map(int, line.split()))
        if len(x) != 2 + WIDTH*2:
            raise ValueError("Wrong number of results per paired game")
        rows.append((x[0], x[1], x[2:2+WIDTH], x[2+WIDTH:]))
    if [(r[0], r[1]) for r in rows] != [(i, s) for i in range(first, first+pairs) for s in (0, 1)]:
        raise ValueError("Missing or duplicate paired games")
    return fixture, rows


def pair_score_interval(wins, games):
    # With two same-seed games, the pair mean lies in [0,1]. Using the
    # Bernoulli variance bound treats one board pair as one independent unit.
    # This additional score interval avoids a zero error bar after a clean sweep.
    n, p, z = games/2, wins/games, 1.959963984540054
    den = 1+z*z/n
    mid = (p+z*z/(2*n))/den
    radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-radius), min(1,mid+radius)]


def portfolio(rows_by_fixture, reference_key):
    # The fixtures are fixed strata. Estimate sampling variability from seed
    # pairs *within* each fixture, preserving seat and entrant matching.
    strata = []
    totals = [[0, 0, 0], [0, 0, 0]]
    for rows in rows_by_fixture.values():
        rows = sorted(rows)
        differences = []
        for i in range(0, len(rows), 2):
            pair = rows[i:i+2]
            for row in pair:
                for j in (0, 1):
                    totals[j][row[2+j][0]] += 1
            differences.append(sum(int(r[3][0] == 0)-int(r[2][0] == 0) for r in pair)/2)
        strata.append(differences)
    delta = statistics.mean(statistics.mean(x) for x in strata)
    ci = None
    if all(len(x) > 1 for x in strata):
        se = math.sqrt(sum(statistics.variance(x)/len(x) for x in strata))/len(strata)
        ci = [max(-1,delta-1.959963984540054*se), min(1,delta+1.959963984540054*se)]
    return {reference_key+"_wins_losses_caps": totals[0], "candidate_wins_losses_caps": totals[1],
            "win_rate_change": delta, "interval_95": ci}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--incumbent", type=Path, default=ROOT/"models"/"adaptive-v1.json")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--rival-seed", type=int, required=True)
    parser.add_argument("--games", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=min(8,os.cpu_count() or 1))
    parser.add_argument("--cap", type=int, default=1000)
    parser.add_argument("--rival-checkpoint", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--opponents", nargs="+", choices=(*FIXTURES,"prior_challenger_v3"))
    parser.add_argument("--baseline-v3", action="store_true", help="Compare with the same v3 assets and previous bidding policy")
    parser.add_argument("--baseline-without-deal", action="store_true",
                        help="Use the same robust entrant with its trade valuation network disabled as reference")
    args = parser.parse_args()
    is_v4=json.loads(args.model.read_bytes())["format"]=="monopoly.adaptive.v4"
    if not args.opponents:
        args.opponents=(*FIXTURES,"prior_challenger_v3") if is_v4 else FIXTURES
    if args.baseline_v3 and (not is_v4 or args.baseline_without_deal):
        parser.error("The v3 reference requires a v4 challenger and a single reference mode")
    if args.games < 2 or args.games%2 or args.cap < 1 or args.workers < 1:
        parser.error("Use an even game count, positive turn cap and positive worker count")
    if len(set(args.opponents)) != len(args.opponents):
        parser.error("A fixture can appear only once")
    historical_seeds = {63289011,64713921,129461997,983260741}
    if args.seed == args.rival_seed or historical_seeds & {args.seed, args.rival_seed}:
        parser.error("Training and qualification require separate, fresh seed namespaces")
    historical = ARCHIVES if is_v4 or "prior_challenger_v3" in args.opponents else {k:v for k,v in ARCHIVES.items() if k!="prior_challenger_v3"}
    protected = [ROOT/"reports"/(stem+".json") for pair in historical.values() for stem in pair]
    protected += [args.model, args.incumbent]
    if (any(p.resolve() in (args.rival_checkpoint.resolve(), args.report.resolve()) for p in protected) or
            args.rival_checkpoint.resolve() == args.report.resolve()):
        parser.error("Use independent destinations for the new report and challenger")
    engine_hash = engine_digest()
    subprocess.run(["make", "build/league_bench"], cwd=ROOT, check=True)
    if engine_digest() != engine_hash:
        raise RuntimeError("The engine source changed during compilation. Retry from a frozen tree.")
    candidate_hash, baseline_hash, executable_hash = map(digest,(args.model,args.incumbent,BINARY))
    files, model, version = entrant_inputs(args.incumbent, args.model)
    if args.baseline_without_deal and (version != "monopoly.adaptive.v3" or len(files) != 4):
        parser.error("The trade-network ablation requires a v3 entrant with the deal_value network")
    reference_key = "v3_reference" if args.baseline_v3 else "candidate_without_deal" if args.baseline_without_deal else "incumbent"
    reference_stream = " without_deal" if args.baseline_without_deal else " "+reference_key
    inputs = {name: model+reference_stream for name in args.opponents}
    rivals = {}
    for name, (checkpoint, history) in ARCHIVES.items():
        if name not in inputs:
            continue
        path, statement = [ROOT/"reports"/(s+".json") for s in (checkpoint,history)]
        fixed = read_known_rival(path, statement, args.cap)
        inputs[name] += " "+serialized_rival(fixed)
        rivals[name] = {"seed": fixed["seed"], "weights": fixed["weights"],
                        "target_manifest_sha256": fixed["target_manifest_sha256"],
                        "checkpoint_sha256": digest(path), "archived_report_sha256": digest(statement)}
    if "learned_rival" in inputs:
        path = args.rival_checkpoint
        if path.exists():
            fresh = json.loads(path.read_bytes())
            if (fresh["target_manifest_sha256"], fresh["engine_sha256"], fresh["seed"], fresh["turn_cap"]) != (
                    candidate_hash, engine_hash, args.rival_seed, args.cap):
                raise ValueError("Existing rival checkpoint belongs to another experiment")
        else:
            fresh = train_challenger(BINARY, files, model+" incumbent", args.cap, args.workers, args.rival_seed,
                                     candidate_hash, engine_hash, path, entrant_label=version)
        inputs["learned_rival"] += " "+serialized_rival(fresh)
        rivals["learned_rival"] = {k: fresh[k] for k in ("seed","weights","training_games","scope","target_manifest_sha256")}
        rivals["learned_rival"]["checkpoint_sha256"] = digest(path)
    pairs = args.games//2
    rows = defaultdict(list)
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(chunk, f, i, min(20,pairs-i), args, files, inputs[f])
                   for i in range(0,pairs,20) for f in args.opponents]
        for future in as_completed(futures):
            name, data = future.result()
            rows[name].extend(data)
    results = {}
    for name in args.opponents:
        raw = summarize(rows[name])
        for arm in ("frozen","adaptive"):
            arm_stats = raw[arm]
            arm_stats["pair_score_interval_95"] = pair_score_interval(arm_stats["wins"],arm_stats["games"])
        results[name] = {reference_key: raw["frozen"], "candidate": raw["adaptive"],
                         "win_rate_change": raw["win_rate_change"],
                         reference_key+"_to_candidate_outcomes": raw["outcomes_frozen_to_adaptive"]}
    if "incumbent_v1" in results and reference_key=="incumbent":
        mirror = results["incumbent_v1"]["incumbent"]
        if mirror["wins"] != mirror["losses"]:
            raise RuntimeError("Incumbent seat-swapped self-match broke mirror symmetry")
    if version == "monopoly.adaptive.v1" and any(r[2] != r[3] for rr in rows.values() for r in rr):
        raise RuntimeError("Identical v1 entrants produced different paired trajectories")
    if (engine_digest(),digest(args.model),digest(args.incumbent),digest(BINARY)) != (
            engine_hash,candidate_hash,baseline_hash,executable_hash) or entrant_inputs(args.incumbent,args.model) != (files,model,version):
        raise RuntimeError("An input changed during qualification")
    baseline_desc="same candidate with deal valuation network disabled" if args.baseline_without_deal else "monopoly.adaptive.v3" if args.baseline_v3 else "monopoly.adaptive.v1"
    reference_hash=baseline_hash
    if args.baseline_without_deal:
        reference_hash=candidate_hash
    if args.baseline_v3:
        v3_path=ROOT/"models/adaptive-v3.json"
        frozen_v3=json.loads(v3_path.read_bytes())
        candidate_fields=json.loads(args.model.read_bytes())
        if any(candidate_fields[k]!=v for k,v in frozen_v3.items() if k.endswith("_sha256")):
            raise ValueError("The reconstructed v3 reference differs from the archived v3 checkpoint")
        reference_hash=digest(v3_path)
    report = {"format":"monopoly.league.v4" if is_v4 else "monopoly.league.v3", "entrant":version, "baseline":baseline_desc,
              "seed":args.seed, "rival_seed":args.rival_seed, "games_per_entrant_and_fixture":args.games,
              "turn_cap":args.cap, "manifest_sha256":candidate_hash, "baseline_manifest_sha256":reference_hash,
              "baseline_deal_net_enabled":args.baseline_v3,
              "engine_sha256":engine_hash, "binary_sha256":executable_hash, "evaluator_sha256":digest(Path(__file__)),
              "method":"Outright bankruptcies win. Turn caps are nonwins. Per fixture, both entrants receive the same board and opponent conditions in both seats. Normal 95% intervals use independent seed-pair means or within-pair differences. An additional score interval bounds pair variance using one independent unit per board, including after a clean sweep. The portfolio uses equal fixed fixture weights and within-fixture uncertainty; intervals have no multiple-comparison correction.",
              "fresh_rival":"A separate bounded six-parameter evolutionary search maximizes the rival's outright wins against the locked candidate. Its seed never provides qualification boards.",
              "fixture_definitions":{"known_additive":"Original development family 5 with public price premiums, variable reserve and negotiation/management modes.",
                  "additive_liquidity":"New trade-free bidder, face and mortgage baseline plus group premiums; cash reserve follows next-roll rent exposure; cautious manager.",
                  "additive_district":"New trade-free bidder, face baseline plus development return, district denial, bank house supply and railroad premiums; builder management.",
                  "changing":"Trading-then-refusal and reverse switch once on a private fixture seed. Reactive closes after two personally unacceptable offers and reopens after cooldown."},
              "rivals":rivals, "equal_fixture_weighted":portfolio(rows, reference_key), "results":results}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    temp=args.report.with_suffix(".pending")
    temp.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    temp.replace(args.report)
    for name,r in results.items():
        a,b=r[reference_key]["win_rate"]["estimate"],r["candidate"]["win_rate"]["estimate"]
        print(f"{name:26} reference {a:7.2%}  candidate {b:7.2%}  change {b-a:+.2%}")


if __name__=="__main__":
    main()
