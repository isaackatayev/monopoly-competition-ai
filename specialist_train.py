"""Train auction and acquisition policies by paired, complete Monopoly matches."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
NAMES = ["deed_collateral", "uncontested_start", "set_progress", "completed_set", "rival_set",
         "split_deed", "railroad", "railroad_growth", "utility", "cash_pressure",
         "price_pressure", "opening_cash", "building_cash", "first_option", "bidder_prior",
         "bidder_variation", "neural_equity", "neural_scale", "trade_switch", "market_caution"]
SOURCES = ["board", "economy", "policy", "search", "holdout", "neural_policy", "neural_trade",
           "neural_value", "response", "opponent", "adaptive_policy", "robust_policy", "pressure_policy", "deal_value", "specialist_arena"]
BASE = [.50, .65, .65, .65, .65, .40, .68, .65, .45, .45,
        .73, .65, .30, .58, .80, .40, .30, .55, .55, .40]
PRIOR_NAMES = ["acquisition", "completion", "denial", "railroads", "auction", "reserve", "development",
               "financing", "trade_demand", "trade_defense", "jail_patience", "house_scarcity"]
VEC = C.c_double * 20
OLD = C.c_double * 12
OUT = C.c_int * 4


def stream(*key):
    return int.from_bytes(sha256(repr(key).encode()).digest()[:8], "little")


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


class Arena:
    def __init__(self, library, workers, directory=ROOT, opponent=None, model_directory=None):
        model_dir = Path(model_directory or (ROOT/"models"))
        manifest_path = model_dir/"neural.json"
        manifest = json.loads(manifest_path.read_text())
        value_file = model_dir/manifest["value_network"]
        response_file = model_dir/manifest["response_network"]
        if sha256(value_file.read_bytes()).hexdigest() != manifest["sha256"] or (
                sha256(response_file.read_bytes()).hexdigest() != manifest["response_sha256"]):
            raise ValueError("Frozen neural model files do not match their manifest")
        self.workers = ThreadPoolExecutor(workers)
        inputs = [directory/"src"/(s+".cpp") for s in SOURCES]
        inputs += [directory/"src"/s for s in ("game.hpp", "groups.hpp", "adaptive_policy.hpp",
                   "neural_value.hpp", "neural_trade.hpp", "response.hpp", "opponent.hpp")]
        inputs += [Path(__file__), manifest_path, value_file, response_file]
        if opponent:
            inputs.append(opponent)
        self.hashes = {path.name: sha256(path.read_bytes()).hexdigest() for path in inputs}
        cmd = [os.environ.get("CXX", "c++"), "-O3", "-std=c++17", "-fPIC", "-shared", "-Isrc"]
        subprocess.run(cmd + [str(directory/"src"/(s+".cpp")) for s in SOURCES] +
                       ["-o", str(library)], cwd=directory, check=True)
        self.lib = C.CDLL(str(library))
        self.lib.specialist_load.argtypes = [C.c_char_p]*3
        self.lib.specialist_batch.argtypes = [C.POINTER(C.c_double), C.POINTER(C.c_double),
                                              C.c_int, C.c_int, C.c_uint64, C.c_int, C.c_int,
                                              C.POINTER(C.c_int)]
        self.lib.specialist_batch.restype = None
        files = [value_file, response_file, opponent]
        if not self.lib.specialist_load(*(os.fsencode(p) if p else b"" for p in files)):
            raise ValueError("Could not load specialist training networks")
        self.prior = OLD(*(manifest["prior_weights"][key] for key in PRIOR_NAMES))

    def batch(self, vec, phase, rival, seed, n, cap):
        out = OUT()
        self.lib.specialist_batch(VEC(*vec), self.prior, phase, rival, seed, n, cap, out)
        return list(out)

    def tournament(self, pop, phase, opponents, n, seed, cap=1000):
        result = [[[0]*4 for _ in opponents] for _ in pop]
        jobs = [(i,j,self.workers.submit(self.batch, w, phase, rival, stream(seed,j,off), min(100,n-off),cap))
                for i,w in enumerate(pop) for j,rival in enumerate(opponents) for off in range(0,n,100)]
        for i,j,f in jobs:
            for k,v in enumerate(f.result()):
                result[i][j][k] += v
        return result


def train(arena, args):
    rng = random.Random(args.seed)
    champ = BASE[:]
    center, spread = champ[:], [.30]*len(champ)
    history=[]
    for gen in range(args.generations):
        # Broad fast matches learn acquisition first. Later generations use actual network
        # afterstates. The last generation uses the full deployed neural policy and trades.
        phase = 0 if gen < args.generations*0.8 else (1 if gen+1<args.generations else 2)
        pop = [champ]
        for k in range(1,args.population):
            scale = .52 if k<=3 else 1.
            w = [min(1.,max(0.,rng.gauss(v,sigma*scale))) for v,sigma in zip(champ if k<=5 else center,spread)]
            if k==args.population-1 and gen%2==0: w=[rng.random() for _ in center]
            pop.append(w)
        rivals = [0,1,2,3,4+gen%3]
        n = args.per_cell
        rows=arena.tournament(pop,phase,rivals,n,stream("specialist-train-v1",args.seed,gen))
        def fitness(i):
            rates=[(x[0]+.10*x[2])/n for x in rows[i]]
            return sum(x*w for x,w in zip(rates,[.43,.18,.12,.18,.09]))
        order=sorted(range(len(pop)),key=fitness,reverse=True)
        champ=pop[order[0]]
        elite=[pop[i] for i in order[:max(2,args.population//4)]]
        for j in range(len(champ)):
            mean=sum(w[j] for w in elite)/len(elite)
            sd=math.sqrt(sum((w[j]-mean)**2 for w in elite)/len(elite))
            center[j]=.23*center[j]+.77*mean
            spread[j]=max(.065,.57*spread[j]+.43*sd)
        history.append(dict(generation=gen+1,phase=phase,rivals=rivals,weights=pop,results=rows,selected=order[0]))
        games=(gen+1)*len(pop)*len(rivals)*n
        dump(args.report,dict(format="monopoly.specialist.training.v1",seed=args.seed,completed_games=games,
             population=args.population,per_cell=n,turn_cap=1000,training_generations=history,parameter_order=NAMES,
             input_sha256=arena.hashes,selection_objective=dict(
                 opponent_weights=[.43,.18,.12,.18,.09],win=1,unresolved=.1,loss=0),
             phases=["Fast full-match outcomes with inexpensive continuation and trading policies",
                     "Neural acquisition afterstates with inexpensive continuation and trading policies",
                     "Full deployed neural decision and trading policies with the behavior model when supplied"],
             seed_design="Each comparison swaps the starting seat on the same game and perturbation seed."))
        dump(args.output,dict(format="monopoly.specialist.v1",weights=champ,parameter_order=NAMES,training_games=games,
             training_seed=args.seed,training_roster=["known_blocker","perturbed_blocker","no_trade_proxy",
             "legacy","rotating_collector_builder_cautious"]))
        print(f"Generation {gen+1}/{args.generations} phase={phase} games={games} "
              f"fitness={fitness(order[0]):.4f} wins={[x[0]/n for x in rows[order[0]]]}",flush=True)


def options():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed",type=int,default=428195)
    p.add_argument("--generations",type=int,default=20)
    p.add_argument("--population",type=int,default=10)
    p.add_argument("--per-cell",type=int,default=1000)
    p.add_argument("--workers",type=int,default=min(8,os.cpu_count() or 1))
    p.add_argument("--model-directory",type=Path,default=ROOT/"models",
                   help="Directory containing the frozen neural manifest and its binary networks")
    p.add_argument("--opponent-net",type=Path,help="Defaults to opponent.bin in the model directory")
    p.add_argument("--output",type=Path,default=ROOT/"models"/"specialist.json")
    p.add_argument("--report",type=Path,default=ROOT/"reports"/"specialist-training.json")
    return p.parse_args()


if __name__=="__main__":
    args=options()
    if args.per_cell<2 or args.per_cell%2 or args.population<4 or args.generations<2 or args.workers<1:
        raise SystemExit("Use an even per-cell game count, at least four policies and two generations")
    with tempfile.TemporaryDirectory(prefix="monopoly-specialist-") as tmp:
        opponent = args.opponent_net or (args.model_directory/"opponent.bin")
        arena = Arena(Path(tmp)/"trainer.so",args.workers,opponent=opponent,model_directory=args.model_directory)
        train(arena,args)
