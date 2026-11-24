"""Evaluate the deployed rematch selector on complete game outcomes and matched boards."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import statistics
import subprocess
import tempfile
import zlib

from arena import example
import native
from series_player import competition_player, SeriesPlayer

ROOT = Path(__file__).resolve().parent
RIVALS = ("qualification-rival", "qualification-rival-v2", "qualification-rival-v3")
# Planned before seeing any outcome. The changing rival retains its opaque host ID.
SCENARIOS = (("archived_v1",0,0), ("archived_v2",1,1), ("archived_v3",2,2), ("v1_then_v3",0,2))
FRESH_RIVALS = ("qualification-rival-v4", "qualification-rival-v4-b", "qualification-rival-v4-c")
FRESH_SCENARIOS = (("fresh_v4",0,0), ("fresh_v4_b",1,1), ("fresh_v4_c",2,2))
FRESH_BOARD_SEED = 493578244
FRESH_SELECTOR_SEED = 831920953
DEFAULT_BOARD_SEED = 844728927
DEFAULT_SELECTOR_SEED = 535761042


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def token(root, domain, *indices):
    material = json.dumps([root,domain,*indices],separators=(",",":")).encode()
    return hashlib.sha256(material).digest()


def fingerprint(profile):
    return (profile[0],tuple(profile[1]),*(getattr(part,"sha256",None) for part in profile[2:]))


def manifest(initial):
    if initial in ("champion","adaptive","neural"):
        return native.ADAPTIVE_MODEL
    if initial in ("adaptive-v1","adaptive-v2","adaptive-v3","adaptive-v4","pressure"):
        return ROOT/"models"/(("pressure-player" if initial=="pressure" else initial)+".json")
    return Path(initial).resolve()


def identify(profiles, initial):
    names = ("adaptive-v1","adaptive-v2","adaptive-v3","adaptive-v4","pressure",initial)
    available = {}
    for name in names:
        path = manifest(name)
        if path.exists():
            key = fingerprint(native.model(name))
            available.setdefault(key,(name,path))
    labels, info = [], {}
    for profile in profiles:
        name,path = available[fingerprint(profile)]
        if name in labels:
            raise ValueError("Series selector contains duplicate checkpoints")
        labels.append(name)
        info[name] = {"style":profile[0],"manifest":os.path.relpath(path,ROOT),"sha256":sha(path),
                      "component_sha256":[getattr(component,"sha256",None) for component in profile[2:]]}
    return labels,info


@dataclass(frozen=True)
class Board:
    seed: int
    seat: int
    rival: int


def make_series(args,cases):
    scenarios = []
    seen = set()
    for scenario,(_,first,second) in enumerate(cases):
        series = []
        for replicate in range(args.series):
            start_seat = token(args.board_seed,"seat",scenario,replicate)[0] & 1
            games = []
            for t in range(args.length):
                seed = int.from_bytes(token(args.board_seed,"initial-board",scenario,replicate,t)[:8],"little")
                if seed in seen:
                    raise RuntimeError("Repeated initial game seed")
                seen.add(seed)
                games.append(Board(seed,start_seat^(t%2),first if t<args.length//2 else second))
            series.append(games)
        scenarios.append(series)
    return scenarios


def plugin(directory):
    out = Path(directory)/"series_outcomes.so"
    command = shlex.split(os.environ.get("CXX","c++"))
    subprocess.run([*command,"-std=c++17","-O3","-fPIC","-shared",str(ROOT/"src/series_bench.cpp"),
                    str(ROOT/"build/libmonopoly.so"),"-o",str(out)],check=True,cwd=ROOT)
    lib = C.CDLL(str(out))
    lib.mono_series_config_size.restype = C.c_size_t
    fields = ("style","weights","net","response","opponent","specialist","resilience","robust","deal_net","pressure")
    if tuple(k for k,_ in native.Config._fields_) != fields or lib.mono_series_config_size() != C.sizeof(native.Config):
        raise RuntimeError("Native evaluation plugin has a different Config ABI")
    lib.mono_series_outcomes.argtypes = [native.Config,native.Config,C.POINTER(C.c_double),C.POINTER(C.c_uint64),
                                         C.POINTER(C.c_uint8),C.c_int,C.c_int,C.POINTER(C.c_uint8)]
    lib.mono_series_outcomes.restype = None
    return lib


def outcome_tape(boards, profiles, weights, workers, cap):
    frozen = native.model("frozen")
    by_rival = [[i for i,b in enumerate(boards) if b.rival==r] for r in range(len(weights))]
    tape = [bytearray([255]*len(profiles)) for _ in boards]
    with tempfile.TemporaryDirectory(prefix="monopoly-series-") as tmp:
        lib = plugin(tmp)
        def run(job):
            arm,rival,indices = job
            n = len(indices)
            board = (C.c_uint64*n)(*(boards[i].seed for i in indices))
            seat = (C.c_uint8*n)(*(boards[i].seat for i in indices))
            results = (C.c_uint8*n)()
            lib.mono_series_outcomes(native.config(profiles[arm]),native.config(frozen),weights[rival],
                                     board,seat,n,cap,results)
            if any(x>2 for x in results):
                raise RuntimeError("Invalid completed-game outcome")
            return arm,indices,bytes(results)
        jobs = [(arm,rival,idx[start:start+128]) for arm in range(len(profiles))
                for rival,idx in enumerate(by_rival) for start in range(0,len(idx),128)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for arm,indices,results in pool.map(run,jobs):
                for idx,result in zip(indices,results):
                    tape[idx][arm] = result
    if any(max(row)>2 for row in tape):
        raise RuntimeError("Incomplete counterfactual tape")
    return tape


def count(outcomes):
    return [outcomes.count(i) for i in range(3)]


def summarize(series_values, draws):
    def metric(xs):
        wins,losses,caps = map(sum,zip(*(count(s) for s in xs)))
        rates = [s.count(0)/len(s) for s in xs]
        return {"wins":wins,"losses":losses,"caps":caps,"win_rate":wins/(wins+losses+caps),
                "score_with_half_for_cap":(wins+.5*caps)/(wins+losses+caps),
                "win_rate_95pct_series_interval":interval(rates,draws)}
    comparison = {k:metric(v) for k,v in series_values.items()}
    live = series_values["live"]
    for k,values in series_values.items():
        if k == "live": continue
        changes = [(left.count(0)-right.count(0))/len(left) for left,right in zip(live,values)]
        comparison[k]["live_minus_this_win_rate"] = statistics.mean(changes)
        comparison[k]["paired_difference_95pct_series_interval"] = interval(changes,draws)
    return comparison


def interval(values,draws):
    if len(set(values)) == 1:
        return None  # A zero observed variance does not bound an unobserved change.
    means = sorted(sum(values[i] for i in selection)/len(values) for selection in draws)
    return [means[int(.025*len(means))],means[int(.975*len(means))]]


def replay_scenario(args,scenario,series,offset,tape,profiles,labels):
    names = ["live","uniform"]+labels
    series_outcomes = {k:[] for k in names}
    choices = []
    histograms = []
    starting = example("buy")
    starting["state"].update(position=[0,0],turn=1,active=0)
    starting["query"] = {"kind":"manage"}
    n = args.length
    for rep,boards in enumerate(series):
        live = SeriesPlayer(profiles)
        opp = token(args.selector_seed,"opponent",scenario,rep).hex()[:24]
        previous, previous_winner = None,None
        row = {k:[] for k in names}
        picked = []
        for idx,board in enumerate(boards):
            game = token(args.selector_seed,"public-game",scenario,rep,idx).hex()[:24]
            packet = {**starting,"game_id":game,"request_id":"opening","player":board.seat,"opponent_id":opp}
            if previous is not None:
                packet.update(previous_game_id=previous,previous_winner=previous_winner)
            # The actual deployed chooser sees a valid pre-roll management packet
            # and only its selected predecessor's winner. No arm's counterfactual
            # is passed to choose. All profiles pass this undeveloped opening.
            reply = live.choose(packet)
            if reply["action"]["value"] or reply["game_id"] != game:
                raise RuntimeError("Unexpected opening management response")
            arm = live.games[game,board.seat].profile
            if idx==0 and arm!=0:
                raise RuntimeError("A new series did not start with its designated primary model")
            options = tape[offset+rep*n+idx]
            result = options[arm]
            picked.append(arm)
            previous, previous_winner = game, board.seat if result==0 else 1-board.seat if result==1 else None
            row["live"].append(result)
            uniform = 0 if idx==0 else random.Random(token(args.selector_seed,"uniform",scenario,rep,idx)).randrange(len(profiles))
            row["uniform"].append(options[uniform])
            for j,key in enumerate(labels): row[key].append(options[j])
        for key in names: series_outcomes[key].append(row[key])
        choices.append(picked)
        histograms.append({"series":rep,"wins_losses_caps":{k:count(v) for k,v in row.items()},
                           "arm_counts":[picked.count(arm) for arm in range(len(profiles))]})
    rng = random.Random(token(args.selector_seed,"intervals",scenario))
    draws = [rng.choices(range(args.series),k=args.series) for _ in range(2000)]
    full = summarize(series_outcomes,draws)
    width = max(1,n//8)
    segments = {"opening":[0,width],"end_first_half":[n//2-width,n//2],
                "start_second_half":[n//2,n//2+width],"end":[n-width,n],
                "first_half":[0,n//2],"second_half":[n//2,n]}
    windows = {}
    for key,(start,end) in segments.items():
        freq = [sum(p[start:end].count(i) for p in choices) for i in range(len(labels))]
        windows[key] = {"game_indices_zero_based_half_open":[start,end],"live_arm_counts":dict(zip(labels,freq)),
                        "win_rates":{name:sum(row[start:end].count(0) for row in values)/(args.series*(end-start))
                                     for name,values in series_outcomes.items()}}
    raw = b"".join(tape[offset:offset+len(series)*n])
    return {"comparison":full, "time_windows":windows,"per_series":histograms,
            "counterfactual_tape_sha256":hashlib.sha256(raw).hexdigest(),
            "counterfactual_tape_zlib_base64":base64.b64encode(zlib.compress(raw,9)).decode()}


def run(args):
    archives,cases = (FRESH_RIVALS,FRESH_SCENARIOS) if args.fresh_qualifiers else (RIVALS,SCENARIOS)
    player = competition_player(args.initial,True)
    profiles = player.profiles
    labels,models = identify(profiles,args.initial)
    if args.require_pressure and not any(p[0]==10 for p in profiles):
        raise ValueError("The deployed series chooser must contain the pressure profile")
    opponent_paths = [ROOT/"reports"/(r+".json") for r in archives]
    rivals = [json.loads(p.read_bytes()) for p in opponent_paths]
    if any(r.get("format")!="monopoly.fixture.challenger.v1" or r["turn_cap"]!=args.cap
           or len(r["weights"])!=6 or any(not 0<=v<=1 for v in r["weights"]) for r in rivals):
        raise ValueError("A challenger archive has the wrong shape or turn cap")
    if args.fresh_qualifiers:
        target = sha(manifest("adaptive-v4"))
        if any(r["target_manifest_sha256"]!=target for r in rivals) or len({r["seed"] for r in rivals})!=3:
            raise ValueError("All three fresh archives must target the locked v4 with distinct training seeds")
        if {args.board_seed,args.selector_seed}.intersection({r["seed"] for r in rivals} | {DEFAULT_BOARD_SEED,DEFAULT_SELECTOR_SEED}):
            raise ValueError("The fresh series streams must be distinct from training and archived evaluation seeds")
    weights = [(C.c_double*6)(*r["weights"]) for r in rivals]
    files = [ROOT/f for f in ("build/libmonopoly.so","src/adaptive_bench.cpp","src/series_bench.cpp",
             "src/evaluation/policy.hpp","series_player.py","live_player.py","series_evaluate.py","models/neural.json")]
    files += [ROOT/v["manifest"] for v in models.values()] + opponent_paths
    hashes = {os.path.relpath(p,ROOT):sha(p) for p in files}
    scenarios = make_series(args,cases)
    flat = [b for scenario in scenarios for s in scenario for b in s]
    tape = outcome_tape(flat,profiles,weights,args.workers,args.cap)
    out = {}
    for i,(name,first,second) in enumerate(cases):
        out[name] = replay_scenario(args,i,scenarios[i],i*args.series*args.length,tape,profiles,labels)
        out[name].update(first_half_opponent=archives[first],second_half_opponent=archives[second])
    if hashes != {os.path.relpath(p,ROOT):sha(p) for p in files}:
        raise RuntimeError("An evaluation input changed during the run")
    return {"format":"monopoly.series-learning.v1","qualification":False,"initial":args.initial,
        "suite":"three fresh locked v4 challengers" if args.fresh_qualifiers else "archived challengers and unannounced switch",
        "board_seed":args.board_seed,"selector_seed":args.selector_seed,"series_per_scenario":args.series,
        "games_per_series":args.length,"turn_cap":args.cap,"completed_or_capped_native_duels":len(flat)*len(profiles),
        "decision_opportunities":len(flat),"profile_order":labels,"profile_manifests":models,"input_sha256":hashes,
        "opponents":{stem:{"weights":r["weights"],"sha256":sha(path),"training_seed":r["seed"],
                           "target_manifest_sha256":r["target_manifest_sha256"]}
                     for stem,r,path in zip(archives,rivals,opponent_paths)},
        "design":"Each independent series starts with the first live profile and no evidence. Each matchup is a full scalar-format game with the original archived rival callback. All arms, fixed baselines and a uniform chooser replay the same board seeds and seats. The uniform chooser starts with the designated primary and draws uniformly thereafter. No board repeats. Seat alternates with independently varied first seat.",
        "observations":"Invokes the real SeriesPlayer.choose on a valid opening of each linked game with opaque independently generated game/opponent IDs, current seat, and only the prior selected arm's publicly reported winner. The opponent ID stays unchanged at the unannounced switch. The other arms' outcomes remain offline. Native duels start fresh each game to isolate profile selection from separate linked-game action-history transfer.",
        "scoring":"Strict wins are the primary endpoint. A cap is a reported null winner, which the deployed selector treats as a non-win. Half-credit cap scores are also shown.",
        "uncertainty":"95% bootstrap percentile intervals from 2000 resamplings of complete independent series, paired on all games and arms. Null means all observed series values were identical. Intervals are descriptive per comparison, without multiplicity correction.",
        "tape_encoding":"For each scenario, decompress the base64/zlib string. One byte per arm (0 win, 1 loss, 2 cap) in profile_order, nested by series then game then arm. Seeds and public tokens are independently reproducible using the driver.",
        "reproduce":f"/usr/bin/python3 series_evaluate.py {'--fresh-qualifiers ' if args.fresh_qualifiers else ''}--initial {args.initial} --workers {args.workers} --series {args.series} --length {args.length} --cap {args.cap} --board-seed {args.board_seed} --selector-seed {args.selector_seed}",
        "scenarios":out}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial",default="champion")
    parser.add_argument("--workers",type=int,default=3)
    parser.add_argument("--series",type=int)
    parser.add_argument("--length",type=int)
    parser.add_argument("--cap",type=int,default=1000)
    parser.add_argument("--board-seed",type=int)
    parser.add_argument("--selector-seed",type=int)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--fresh-qualifiers",action="store_true",help="Run all three independently trained v4 challengers on fresh streams")
    parser.add_argument("--require-pressure",action="store_true")
    args = parser.parse_args()
    fresh = args.fresh_qualifiers
    if args.series is None: args.series = 12 if fresh else 32
    if args.length is None: args.length = 120 if fresh else 160
    if args.board_seed is None: args.board_seed = FRESH_BOARD_SEED if fresh else DEFAULT_BOARD_SEED
    if args.selector_seed is None: args.selector_seed = FRESH_SELECTOR_SEED if fresh else DEFAULT_SELECTOR_SEED
    if args.output is None: args.output = ROOT/"reports"/("series-generalization.json" if fresh else "series-learning.json")
    if args.workers<1 or args.series<2 or args.length<8 or args.length%4 or args.board_seed==args.selector_seed:
        parser.error("Use independent board/selector seeds, positive workers, at least two series, and a length divisible by four and at least eight.")
    report = run(args)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile("w",dir=args.output.parent,delete=False) as out:
        json.dump(report,out,indent=2)
        out.write("\n")
    os.replace(out.name,args.output)
    for name,row in report["scenarios"].items():
        print(name,{k:round(v["win_rate"],4) for k,v in row["comparison"].items()})
