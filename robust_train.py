"""Fit an acquisition and finance policy against two known challengers and mixed bidder populations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
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

NAMES=["previous_rival_mass","recent_rival_mass","additive_mass","open_population_mass",
       "deed_income","rival_spending","cash_stress","first_purchase_right","working_cash",
       "housing_priority","value_network_mix","worst_outcome_weight"]
ROSTER=["previous_qualifier","variable_neural","aggressive_neural","blocker","searched_legacy",
        "additive_pricing","frozen_neural","cautious","hybrid_pricing","recent_qualifier","unseen_nonlinear"]
CONTROL=[-1.]+[0.]*11
DEPLOYED=[-2.]+[0.]*11
FIT_ROSTER=[0,9,5,1,3,4]
AUDIT_ROSTER=[0,9,5,1,2,3,4,6,8,10]
DEPLOYED_MODE=4
DP=C.POINTER(C.c_double)

class Arena:
    def __init__(self,tmp,models,r1,r2,workers):
        path=models/"adaptive-v2.json"
        if not path.exists(): path=models/"adaptive.json"
        adaptive=json.loads(path.read_text())
        def verified(parent,name,expected):
            f=parent/name
            if sha256(f.read_bytes()).hexdigest()!=expected:raise ValueError(f"Checksum mismatch for {f}")
            return f
        b=verified(path.parent,adaptive["base"],adaptive["base_sha256"]);base=json.loads(b.read_text())
        sp=verified(path.parent,adaptive["specialist"],adaptive["specialist_sha256"])
        if "resilience" in adaptive:
            rp=verified(path.parent,adaptive["resilience"],adaptive["resilience_sha256"])
        else:
            # An in-progress training directory can hold a v1 release plus the
            # newly fitted, unpublished resilience stage.
            rp=models/"resilience.json"
            if json.loads(rp.read_text()).get("format")!="monopoly.resilience.v1":
                raise ValueError(f"Invalid resilience stage in {rp}")
        v=verified(b.parent,base["value_network"],base["sha256"])
        o=verified(path.parent,adaptive["opponent"],adaptive["opponent_sha256"])
        r=verified(b.parent,base["response_network"],base["response_sha256"])
        self.models=[(C.c_double*12)(*(base["prior_weights"][key] for key in PRIOR_NAMES)),
                     (C.c_double*20)(*json.loads(sp.read_text())["weights"]),
                     (C.c_double*8)(*json.loads(rp.read_text())["weights"]),
                     (C.c_double*6)(*json.loads(r1.read_text())["weights"]),
                     (C.c_double*6)(*json.loads(r2.read_text())["weights"])]
        src=[ROOT/"src"/(s+".cpp") for s in SOURCES if s not in ("specialist_arena","robust_policy","robust_arena")]
        src += [ROOT/"src"/"robust_policy.cpp",ROOT/"src"/"robust_arena.cpp"]
        inputs=src + sorted((ROOT/"src").glob("*.hpp"))+[Path(__file__),path,b,sp,rp,v,o,r,r1,r2]
        self.hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in inputs}
        lib=tmp/"arena.so"
        subprocess.run([os.environ.get("CXX","c++"),"-O3","-std=c++17","-fPIC","-shared","-Isrc",
                        *map(str,src),"-o",str(lib)],cwd=ROOT,check=True)
        self.lib=C.CDLL(str(lib))
        self.lib.robust_load.argtypes=[C.c_char_p]*3
        self.lib.robust_batch.argtypes=[DP]*6+[C.c_int,C.c_int,C.c_uint64,C.c_int,C.POINTER(C.c_int)]
        self.lib.robust_batch.restype=None
        if not self.lib.robust_load(*(os.fsencode(p) for p in [v,o,r])):raise ValueError("Network load failed")
        self.pool=ThreadPoolExecutor(workers)

    def batch(self,w,op,seed,n,mode=0):
        out=(C.c_int*4)();self.lib.robust_batch((C.c_double*12)(*w),*self.models,op,mode,seed,n,out)
        return list(out)

    def series(self,pop,rivals,n,phase,modes=None):
        rows=[];blocks=[]
        for i,w in enumerate(pop):
            jobs=[[self.pool.submit(self.batch,w,j,stream("robust-portfolio-v1",phase,j,k),min(80,n-k),0 if modes is None else modes[i])
                   for k in range(0,n,80)] for j in rivals]
            data=[[f.result() for f in group] for group in jobs]
            result=[[sum(x) for x in zip(*group)] for group in data]
            rows.append(result);blocks.append(data)
            print(phase,i,[round(r[0]/n,3) for r in result],flush=True)
        return dict(phase=phase,vectors=pop,rivals=rivals,games_per_cell=n,outcomes=rows,blocks=blocks,
                    evidence_modes=modes or [0]*len(pop))

def ranking(phase):
    base=phase["outcomes"][0];n=phase["games_per_cell"]
    def score(i):
        row=phase["outcomes"][i]
        deltas=[(a[0]-b[0])/n for a,b in zip(row,base)]
        rates=[r[0]/n for r in row]
        worst=min(rates[0],rates[1],rates[2])
        return sum(w*d for w,d in zip([.20,.23,.23,.13,.09,.12],deltas))+.18*worst-1.2*max(0,-deltas[5]-.01)
    return sorted(range(1,len(phase["vectors"])),key=score,reverse=True)

def fit_candidates(arena):
    rng=random.Random(5431907)
    pop=[CONTROL,[.52,.58,.68,.18,.57,.69,.31,.58,.68,.48,.32,.15]]
    for i in range(14):
        weights=[rng.uniform(.03,.98) for _ in NAMES]
        if i<3:
            weights[2]=rng.uniform(.7,1)
            weights[8]=rng.uniform(.7,1)
        pop.append(weights)
    initial=arena.series(pop,FIT_ROSTER,320,"search")
    finalists=[CONTROL]+[pop[k] for k in ranking(initial)[:4]]
    selected=arena.series(finalists,FIT_ROSTER,1040,"select")
    primary=finalists[ranking(selected)[0]]
    # Choose a different elite based on its outright wins on the three weak opponents.
    ordered=sorted(range(1,len(finalists)),
                   key=lambda i:-sum(x[0] for x in selected["outcomes"][i][:3]))
    runner_up=next(finalists[k] for k in ordered if finalists[k]!=primary)
    audit=arena.series([CONTROL,primary],AUDIT_ROSTER,2400,"confirm")
    return [initial,selected,audit],primary,runner_up


def portfolio_score(row,n):
    wins=[cell[0]/n for cell in row]
    broad=sum(a*b for a,b in zip(wins,[.20,.23,.23,.13,.09,.12]))
    return .55*broad+.45*min(wins[:3])


def fit_observer(arena,primary,runner_up):
    trials=[(primary,0),(primary,1),(primary,2),(primary,3),(primary,4),
            (runner_up,1),(runner_up,4)]
    weights=[CONTROL]+[w for w,mode in trials]
    modes=[0]+[mode for w,mode in trials]
    select=arena.series(weights,FIT_ROSTER,800,"observer-select",modes)
    n=select["games_per_cell"]
    def score(i):
        rows=select["outcomes"]
        legacy_loss=(rows[0][5][0]-rows[i][5][0])/n
        return portfolio_score(rows[i],n)-.65*max(0,legacy_loss-.01)
    picked=max(range(1,len(weights)),key=score)
    # Deployment has a fixed evidence architecture. Retraining selects the best
    # weights for it even if a different architecture wins an exploratory row.
    fixed=max((i for i in range(len(modes)) if modes[i]==DEPLOYED_MODE),key=score)
    selected=weights[fixed]
    audit=arena.series([DEPLOYED,CONTROL,weights[picked]],AUDIT_ROSTER,2000,
                       "observer-confirm",[0,0,modes[picked]])
    return [select,audit],selected,picked,fixed


def compare_refusal_signals(arena,weights):
    modes=[4,5,6]
    select=arena.series([weights]*len(modes),FIT_ROSTER,1200,"decline-select",modes)
    preferred=max(range(len(modes)),key=lambda i:portfolio_score(select["outcomes"][i],1200))
    audit=arena.series([DEPLOYED,CONTROL,weights],AUDIT_ROSTER,2400,
                       "decline-confirm",[0,0,DEPLOYED_MODE])
    return [select,audit],modes[preferred]


def development_results(final):
    n=final["games_per_cell"]
    result=[]
    for j,op in enumerate(final["rivals"]):
        row=dict(opponent=ROSTER[op],games_per_policy=n)
        for k,label in enumerate(("deployed_style7","optional_style8","candidate")):
            w,l,t,turns=final["outcomes"][k][j]
            row[label]=dict(wins=w,losses=l,turn_caps=t,win_rate=w/n)
        for k,label in enumerate(("against_style7","against_style8")):
            # Seat-swapped games form pairs. The interval uses the differences
            # of thirty independent 80-game paired seed-block proportions.
            diffs=[(a[0]-b[0])/sum(a[:3])
                   for a,b in zip(final["blocks"][2][j],final["blocks"][k][j])]
            mean=statistics.mean(diffs)
            half=1.96*statistics.stdev(diffs)/math.sqrt(len(diffs))
            row[label]=dict(change=mean,paired_ci95=[mean-half,mean+half])
        result.append(row)
    return result


def package(args,hashes,phases,weights,picked,fixed,decline_mode):
    model=dict(format="monopoly.robust.v1",weights=weights,parameter_order=NAMES,
               evidence_mode=DEPLOYED_MODE)
    dump(args.output,model)
    games=lambda phase:len(phase["vectors"])*len(phase["rivals"])*phase["games_per_cell"]
    training=sum(games(p) for p in phases if not p["phase"].endswith("confirm"))
    confirmation=sum(games(p) for p in phases if p["phase"].endswith("confirm"))
    dump(args.report,dict(format="monopoly.robust.training.v1",fixture_names=ROSTER,
         parameter_order=NAMES,selected_model=model,input_sha256=hashes,
         selection_games=training,confirmation_games=confirmation,
         observer_search_winner=picked,observer_deployment_index=fixed,
         decline_signal_search_preference=decline_mode,
         phases=phases,confirmation=development_results(phases[-1]),
         method=("Full 1,000-turn-capped games with both seats and common seed blocks. "
                 "The two historical challenger parameter sets are known training opponents, "
                 "and are two explicit public-price hypotheses inside the agent. Additive and randomized "
                 "neural pricing are training families. Hybrid and nonlinear populations are absent "
                 "from weight searches and first appear during development reporting. "
                 "Unresolved games count as nonwins. No trade-value override was active.")))


def train(arena,args):
    phases,primary,runner_up=fit_candidates(arena)
    observer,weights,picked,fixed=fit_observer(arena,primary,runner_up)
    signals,preferred=compare_refusal_signals(arena,weights)
    package(args,arena.hashes,phases+observer+signals,weights,picked,fixed,preferred)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-directory",type=Path,default=ROOT/"models")
    p.add_argument("--output",type=Path,default=ROOT/"models/robust.json")
    p.add_argument("--report",type=Path,default=ROOT/"reports/robust-training.json")
    p.add_argument("--first-rival",type=Path,default=ROOT/"reports/qualification-rival.json")
    p.add_argument("--second-rival",type=Path,default=ROOT/"reports/qualification-rival-v2.json")
    p.add_argument("--workers",type=int,default=min(8,os.cpu_count() or 1))
    args=p.parse_args()
    with tempfile.TemporaryDirectory(prefix="monopoly-robust-") as tmp:
        train(Arena(Path(tmp),args.model_directory,args.first_rival,args.second_rival,args.workers),args)
