"""Reproduce the two-round neural bidder population search and its paired confirmations."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import ctypes as C
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import tempfile

from specialist_train import PRIOR_NAMES, ROOT, SOURCES, dump, stream

NAMES = ["cold_population_weight", "alternative_population_weight", "neural_bid_mix",
         "premium", "denial_multiplier", "purchase_margin", "neural_equity_mix", "downside_discount"]
RIVALS = ["known_qualification_challenger", "random_neural_bidder", "aggressive_neural_bidder",
          "known_blocker", "rollout_legacy", "additive_pricing", "frozen_neural",
          "cautious", "new_hybrid_pricing"]
CONTROL = [-1.,0.,0.,0.,0.,0.,0.,0.]
DP = C.POINTER(C.c_double)


class Arena:
    def __init__(self, tmp, model_directory, challenger, workers):
        adaptive_path = model_directory/"adaptive.json"
        adaptive = json.loads(adaptive_path.read_text())
        def verified(name, expected, parent=model_directory):
            path = parent/name
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Input checksum mismatch for {name}")
            return path
        base_path = verified(adaptive["base"], adaptive["base_sha256"])
        specialist_path = verified(adaptive["specialist"], adaptive["specialist_sha256"])
        opponent = verified(adaptive["opponent"], adaptive["opponent_sha256"])
        base = json.loads(base_path.read_text())
        value = verified(base["value_network"],base["sha256"],base_path.parent)
        response = verified(base["response_network"],base["response_sha256"],base_path.parent)
        self.prior = (C.c_double*12)(*(base["prior_weights"][k] for k in PRIOR_NAMES))
        self.specialist = (C.c_double*20)(*json.loads(specialist_path.read_text())["weights"])
        self.rival = (C.c_double*6)(*json.loads(challenger.read_text())["weights"])
        sources = [ROOT/"src"/(s+".cpp") for s in SOURCES if s!="specialist_arena"]
        sources.append(ROOT/"src/resilience_arena.cpp")
        inputs = sources + sorted((ROOT/"src").glob("*.hpp"))
        inputs += [Path(__file__), ROOT/"specialist_train.py", adaptive_path, base_path,
                   specialist_path, opponent, value, response, challenger]
        self.hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in inputs}
        library = tmp/"resilience.so"
        subprocess.run([os.environ.get("CXX","c++"), "-O3", "-std=c++17", "-fPIC", "-shared",
                        "-Isrc", *map(str,sources), "-o", str(library)],cwd=ROOT,check=True)
        self.lib = C.CDLL(str(library))
        self.lib.resilience_load.argtypes = [C.c_char_p]*3
        self.lib.resilience_batch.argtypes = [DP,DP,DP,DP,C.c_int,C.c_int,C.c_uint64,C.c_int,C.POINTER(C.c_int)]
        self.lib.resilience_batch.restype = None
        if not self.lib.resilience_load(*(os.fsencode(p) for p in [value,opponent,response])):
            raise ValueError("Could not initialize the frozen networks")
        self.pool = ThreadPoolExecutor(workers)

    def batch(self,w,op,gated,seed,n):
        out=(C.c_int*4)()
        self.lib.resilience_batch((C.c_double*8)(*w),self.prior,self.specialist,self.rival,
                                  op,int(gated),seed,n,out)
        return list(out)

    def series(self,vectors,opponents,n,phase,gated=False):
        rows,blocks=[],[]
        for i,w in enumerate(vectors):
            jobs=[[self.pool.submit(self.batch,w,j,gated,stream("wide-neural-bidders-v1",phase,j,k),min(80,n-k))
                   for k in range(0,n,80)] for j in opponents]
            data=[[f.result() for f in group] for group in jobs]
            result=[[sum(x) for x in zip(*group)] for group in data]
            rows.append(result)
            blocks.append(data)
            print(phase,i,[round(r[0]/n,3) for r in result],flush=True)
        return dict(phase=phase, gated=gated, games_per_cell=n, opponents=opponents,
                    vectors=vectors,outcomes=rows,paired_seed_blocks=blocks)


def rank(data):
    base=data["outcomes"][0]
    scores=[]
    for i,row in enumerate(data["outcomes"]):
        d=[(x[0]-b[0])/data["games_per_cell"] for x,b in zip(row,base)]
        if data["gated"]:
            score=sum(a*b for a,b in zip(d,[.25,.20,.17,.08,.15,.15]))-.8*max(0,-d[4]-.005)
        else:
            score=sum(a*b for a,b in zip(d,[.24,.24,.16,.16,.20]))
            score-=.7*max(0,-d[3]-.005)+.7*max(0,-d[4]-.005)
        scores.append(score)
    return sorted(range(1,len(scores)),key=lambda i:-scores[i])


def comparison(phase):
    block=phase["paired_seed_blocks"]
    n=phase["games_per_cell"]
    rows=[]
    for j,op in enumerate(phase["opponents"]):
        delta=[(a[0]-b[0])/80 for a,b in zip(block[1][j],block[0][j])]
        mean=statistics.mean(delta)
        se=statistics.stdev(delta)/math.sqrt(len(delta))
        rows.append(dict(opponent=RIVALS[op],incumbent=phase["outcomes"][0][j][0]/n,
                         candidate=phase["outcomes"][1][j][0]/n,delta=mean,
                         paired_ci95=[mean-1.96*se,mean+1.96*se]))
    return rows


def portfolio(phase, indices):
    # Every fixture uses independent paired-seed blocks. Preserve the equal weight
    # of each fixture, including opponents the incumbent already beats comfortably.
    block=phase["paired_seed_blocks"]
    means,variances=[],[]
    for j in indices:
        deltas=[(a[0]-b[0])/80 for a,b in zip(block[1][j],block[0][j])]
        means.append(statistics.mean(deltas))
        variances.append(statistics.variance(deltas)/len(deltas))
    mean=statistics.mean(means)
    se=math.sqrt(sum(variances))/len(means)
    return dict(delta=mean,paired_ci95=[mean-1.96*se,mean+1.96*se])


def train(arena,args):
    rng=random.Random(6708995)
    pop=[CONTROL]
    for _ in range(13):
        pop.append([rng.uniform(lo,hi) for lo,hi in [(.04,.40),(.52,.96),(.45,.94),(1.04,1.40),
                    (.85,1.30),(-10,110),(.1,.65),(0,.32)]])
    main=[0,1,2,3,4]
    phases=[arena.series(pop,main,360,"screen")]
    finalists=[CONTROL]+[pop[i] for i in rank(phases[-1])[:3]]
    phases.append(arena.series(finalists,main,1200,"select"))
    first=finalists[rank(phases[-1])[0]]
    phases.append(arena.series([CONTROL,first],list(range(8)),2400,"confirm"))
    phases.append(arena.series(finalists,main+[5],1000,"gated-selection",True))
    winner=finalists[rank(phases[-1])[0]]
    phases.append(arena.series([CONTROL,winner],list(range(9)),2400,"gated-confirm",True))
    def games(p):
        return len(p["vectors"])*len(p["opponents"])*p["games_per_cell"]
    fit=sum(games(p) for p in phases if "confirm" not in p["phase"])
    confirmation=sum(games(p) for p in phases)-fit
    dump(args.output,dict(format="monopoly.resilience.v1",parameter_order=NAMES,weights=winner,
                          selection_games=fit,confirmation_games=confirmation))
    dump(args.report,dict(format="monopoly.resilience.training.v1",sampling_seed=6708995,
         fixture_order=RIVALS,parameter_order=NAMES,selection_games=fit,confirmation_games=confirmation,
         method="Thirteen sampled candidate weight vectors are screened on the old qualifier, randomized neural bidders, blocker and legacy. Three advance to fresh selection. The second round retains the incumbent until public behavior rules out the known blocker, and adds an income-based price scenario. The final confirmation includes a new hybrid fixture.",
         design="Seeds are derived from SHA-256 of phase, fixture and block offset. Each strategy uses the same 80-game block of seat-swapped seeded pairs. Capped games count as nonwins. Intervals use the paired differences of independent block win rates.",
         known_rival="The previously evaluated qualification challenger is an explicitly known training opponent.",
         confirmation=comparison(phases[-1]),first_round=comparison(phases[2]),
         portfolio=dict(all_nine=portfolio(phases[-1],range(9)),
                        without_previously_known_challenger=portfolio(phases[-1],range(1,9))),
         limitations="The additive-pricing fixture was first seen in the first-round confirmation, then included in second-round selection. Its fresh second-round result is reported even when the candidate regresses. The structurally different hybrid was first evaluated in the final development confirmation. This is development evidence; a new adversary must be fitted against the selected deployment.",
         input_sha256=arena.hashes,phases=phases))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-directory",type=Path,default=ROOT/"models")
    parser.add_argument("--output",type=Path,default=ROOT/"models/resilience.json")
    parser.add_argument("--report",type=Path,default=ROOT/"reports/resilience-training.json")
    parser.add_argument("--training-rival",type=Path,default=ROOT/"reports/qualification-rival.json")
    parser.add_argument("--workers",type=int,default=min(8,os.cpu_count() or 1))
    args=parser.parse_args()
    if args.workers<1:parser.error("--workers must be at least one")
    with tempfile.TemporaryDirectory(prefix="monopoly-resilience-") as tmp:
        train(Arena(Path(tmp),args.model_directory,args.training_rival,args.workers),args)
