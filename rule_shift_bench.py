"""Compare frozen entrants under alternative local adjudication and trading rules."""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

import native

ROOT = Path(__file__).resolve().parent
FIXTURES = {"frozen": 461691782, "builder": 999378261, "blocker": 185279622}
REGIMES = ("bankruptcy_1000", "net_worth_100", "bankruptcy_1000_trade_ban_40", "net_worth_100_trade_ban_40")
FIELDS = ("bankruptcy_wins", "bankruptcy_losses", "cap_draws", "net_worth_wins", "net_worth_losses",
          "net_worth_ties", "turns", "reached_shift", "offers", "deals", "offers_from_turn_40", "deals_from_turn_40")
MASK = (1 << 64)-1


class Counts(C.Structure):
    _fields_ = [(name, C.c_int64) for name in FIELDS]


def seeds(root, count):
    # Same initial seed stream as native.Game tournaments in paired mode.
    for _ in range(count):
        root = (root + 0x9e3779b97f4a7c15) & MASK
        x = ((root ^ (root >> 30))*0xbf58476d1ce4e5b9) & MASK
        x = ((x ^ (x >> 27))*0x94d049bb133111eb) & MASK
        yield x ^ (x >> 31)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plugin(directory):
    output = Path(directory)/"rule_shift.so"
    command = shlex.split(os.environ.get("CXX", "c++"))
    subprocess.run([*command, "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-O2", "-fPIC", "-shared",
                    str(ROOT/"src/rule_shift_bench.cpp"), str(ROOT/"build/libmonopoly.so"), "-o", str(output)],
                   check=True, cwd=ROOT)
    lib = C.CDLL(str(output))
    lib.mono_rule_shift_layout.argtypes = [C.c_size_t, C.c_size_t]
    lib.mono_rule_shift_layout.restype = C.c_int
    expected = ["style","weights","net","response","opponent","specialist","resilience","robust","deal_net","pressure"]
    if ([name for name,_ in native.Config._fields_] != expected or
            not lib.mono_rule_shift_layout(C.sizeof(native.Config),C.sizeof(Counts))):
        raise RuntimeError("Evaluation plugin layout or worked net-worth check failed")
    lib.mono_rule_shift_pair.argtypes = [native.Config,native.Config,C.c_uint64,C.POINTER(Counts)]
    lib.mono_rule_shift_pair.restype = None
    return lib


def summarize(raw, games):
    wins = raw["bankruptcy_wins"]+raw["net_worth_wins"]
    losses = raw["bankruptcy_losses"]+raw["net_worth_losses"]
    draws = raw["cap_draws"]+raw["net_worth_ties"]
    if wins+losses+draws != games:
        raise RuntimeError("Missing or duplicated game result")
    return {"games":games, "wins":wins, "losses":losses, "draws":draws, "score":(wins+draws/2)/games, **raw}


def one_fixture(lib, model, rival, start, pairs):
    raw = [{key:0 for key in FIELDS} for _ in REGIMES]
    config, rival_config = native.config(model), native.config(rival)
    for seed in seeds(start,pairs):
        rows = (Counts * len(REGIMES))()
        lib.mono_rule_shift_pair(config,rival_config,seed,rows)
        for totals, row in zip(raw, rows):
            for key in FIELDS:
                totals[key] += getattr(row,key)
    results = {name:summarize(row,2*pairs) for name,row in zip(REGIMES,raw)}
    regular = results["bankruptcy_1000"]
    control = native.batch(model,rival,start,2*pairs,1000,paired=True)
    for key, native_key in (("wins","wins"),("losses","losses"),("cap_draws","caps"),("turns","turns")):
        if regular[key] != control[native_key]:
            raise RuntimeError("Test plugin does not match the native baseline")
    if (regular["deals"] != control["outgoing_accepted"]+control["incoming_accepted"] or
            regular["offers"] != control["offers_sent"]+control["offers_received"]):
        raise RuntimeError("Test plugin trade accounting does not match the native baseline")
    for regime in REGIMES[2:]:
        if results[regime]["offers_from_turn_40"] or results[regime]["deals_from_turn_40"]:
            raise RuntimeError("A banned trade window was used")
    return results


def benchmark(pairs,model="adaptive-v3",seed_offset=0):
    roles=("adaptive-v1",model)
    version=model.rsplit("-",1)[1]
    inputs = {"native_library":"build/libmonopoly.so", "v1_manifest":"models/adaptive-v1.json",
              version+"_manifest":"models/"+model+".json","frozen_manifest":"models/neural.json",
              "evaluation_plugin":"src/rule_shift_bench.cpp","evaluation_policy_abi":"src/evaluation/policy.hpp",
              "evaluation_driver":"rule_shift_bench.py"}
    frozen = {name:sha(ROOT/path) for name,path in inputs.items()}
    entrants = {name:native.model(name) for name in roles}
    used = set()
    fixtures = {}
    with tempfile.TemporaryDirectory(prefix="monopoly-rule-shift-") as tmp:
        lib = plugin(tmp)
        for name,seed in FIXTURES.items():
            seed=(seed+seed_offset)&MASK
            boards = set(seeds(seed,pairs))
            if len(boards) != pairs or used.intersection(boards):
                raise RuntimeError("Repeated initial board seed across fixtures")
            used.update(boards)
            rival = native.model(name)
            versions = {role:one_fixture(lib,entrant,rival,seed,pairs) for role,entrant in entrants.items()}
            differences = {}
            for regime in REGIMES:
                baseline = REGIMES[0]
                differences[regime] = {
                    version+"_minus_v1_score":round(versions[model][regime]["score"]-versions["adaptive-v1"][regime]["score"],8),
                    **{role+"_minus_own_standard":round(rows[regime]["score"]-rows[baseline]["score"],8)
                       for role,rows in versions.items()},
                }
            fixtures[name] = {"seed":seed, "pairs":pairs, "versions":versions, "score_differences":differences}
    if frozen != {name:sha(ROOT/path) for name,path in inputs.items()}:
        raise RuntimeError("An evaluation input changed during the run")
    return {
        "format":"monopoly.rule-shift.v1", "qualification":False,
        "inspiration":{"project":"USC ISI GNOME","url":"https://usc-isi-i2.github.io/gnome/",
                       "relationship":"Independent rule-sensitivity test using our own simulator. It does not run GNOME."},
        "models_frozen":True,
        "scope":"Both entrants use their existing learned parameters and normal public inputs. We test outcome sensitivity to rule changes. No rule identity, horizon, retraining or rule-detection signal is supplied.",
        "game_rules":"US Classic duel engine with its original one-deed-per-side trade format in ordinary allowed windows.",
        "bankruptcy_1000":"Bankruptcy wins immediately. Both solvent after 1000 individual player turns gives each seat half a point.",
        "net_worth_100":"Bankruptcy wins immediately. After 100 individual player turns compare cash plus deed face value, less outstanding mortgage principal on each mortgaged deed, plus half purchase cost for every building level. Hotels count as five levels. Held jail cards have zero adjudication value. Equal dollar totals give each seat half a point.",
        "trade_ban_40":"Where enabled, suppress all trade windows for both seats starting on individual turn 40 inclusive. Purchases, auctions, building and other actions keep the baseline rules.",
        "design":"For each opponent both entrants use exactly the same initial board seeds, twice each with seats reversed. Each game shares the complete board, hidden RNG and decks through turn 39 before trade-rule branches diverge. Each 100-turn result is read from the prefix of its corresponding long game.",
        "scores":"A win receives 1, a draw 0.5, and a loss 0. Trade and turn counters cover both seats. Scores are descriptive for the sampled fixtures.",
        "verification":"Verified bankruptcy control wins, losses, caps, turns, legal offers and closed trades against the production paired batch runner for each matchup. Banned windows produced zero offers from turn 40 onward. Checked net-worth valuation against a worked $745 example.",
        "games_per_cell":2*pairs,"outcomes":len(roles)*len(FIXTURES)*len(REGIMES)*2*pairs,
        "distinct_starting_seeds":len(used),
        "inputs":{key:{"path":value,"sha256":frozen[key]} for key,value in inputs.items()},
        "reproduce":f"make all && python3 rule_shift_bench.py --model {model} --seed-offset {seed_offset} --pairs {pairs}",
        "fixtures":fixtures,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs",type=int,default=100,help="initial seeds per opponent, played in both seats")
    parser.add_argument("--model",choices=("adaptive-v3","adaptive-v4"),default="adaptive-v3")
    parser.add_argument("--seed-offset",type=int,default=0)
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    if args.pairs <= 0:
        parser.error("--pairs must be positive")
    report = benchmark(args.pairs,args.model,args.seed_offset)
    if args.output is None:
        args.output=ROOT/"reports"/("rule-shift-"+args.model.rsplit("-",1)[1]+".json")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",dir=args.output.parent,delete=False) as temp:
        json.dump(report,temp,indent=2)
        temp.write("\n")
    os.replace(temp.name,args.output)
    print(json.dumps({name:row["score_differences"] for name,row in report["fixtures"].items()},indent=2))
