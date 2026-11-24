"""Select a separately loadable competitive bidder and a one-game public-evidence gate."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
from robust_train import Arena as CommonArena, CONTROL, DP, ROSTER
from specialist_train import ROOT, SOURCES, PRIOR_NAMES, dump, stream

PARAMETERS=["bid_multiplier","denial_multiplier","cash_reserve","asset_manager","offer_mode",
            "preservation_gate","blocker_mix","railroad_multiplier","completion_multiplier",
            "pressure_threshold","rival_cash_weight","prior_pressure"]
OPPONENTS=ROSTER+["third_historical_qualifier","high_premium_population","wide_premium_population"]
CONFIRM=[11,0,9,5,12,13,1,3,4,10]


class Arena(CommonArena):
    def __init__(self,tmp,args):
        directory=args.model_directory
        manifest=directory/"adaptive-v3.json"
        if not manifest.exists():manifest=directory/"adaptive.json"
        data=json.loads(manifest.read_text())
        def asset(key,name=None):
            if key not in data:return directory/name
            path=manifest.parent/data[key]
            if sha256(path.read_bytes()).hexdigest()!=data[key+"_sha256"]:
                raise ValueError(f"Checksum mismatch for {path}")
            return path
        base_file=asset("base","neural.json")
        base=json.loads(base_file.read_text())
        value=base_file.parent/base["value_network"]
        response=base_file.parent/base["response_network"]
        if (sha256(value.read_bytes()).hexdigest()!=base["sha256"] or
            sha256(response.read_bytes()).hexdigest()!=base["response_sha256"]):
            raise ValueError(f"Neural network checksum failed in {base_file}")
        specialist=asset("specialist","specialist.json")
        resilience=asset("resilience","resilience.json")
        robust=asset("robust","robust.json")
        deal=asset("deal_value","deal.bin")
        opponent=asset("opponent","opponent.bin")
        def vec(path,n):
            return (C.c_double*n)(*json.loads(path.read_text())["weights"])
        self.models=[(C.c_double*12)(*(base["prior_weights"][k] for k in PRIOR_NAMES)),
                     vec(specialist,20),vec(resilience,8),vec(args.first_rival,6),vec(args.second_rival,6)]
        compiler_inputs=[ROOT/"src"/(n+".cpp") for n in SOURCES if n!="specialist_arena"]
        for file in ("bundle_trade","bundle_match","bundles/verified","pressure_policy","pressure_arena"):
            path=ROOT/"src"/(file+".cpp")
            if path not in compiler_inputs:compiler_inputs.append(path)
        inputs=compiler_inputs+list((ROOT/"src").rglob("*.hpp"))+[ROOT/"src/robust_arena.cpp",manifest,
                base_file,value,response,opponent,specialist,resilience,robust,deal,args.first_rival,
                args.second_rival,args.third_rival,Path(__file__)]
        self.hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in inputs}
        binary=tmp/"pressure.so"
        subprocess.run([os.environ.get("CXX","c++"),"-std=c++17","-O3","-fPIC","-shared","-Isrc",
                        *map(str,compiler_inputs),"-o",str(binary)],cwd=ROOT,check=True)
        self.lib=C.CDLL(str(binary))
        self.lib.robust_load.argtypes=[C.c_char_p]*3
        self.lib.pressure_extras.argtypes=[C.c_char_p,DP,DP]
        self.lib.pressure_batch.argtypes=[DP]*6+[C.c_int,C.c_int,C.c_uint64,C.c_int,C.POINTER(C.c_int)]
        self.lib.pressure_bundle.argtypes=[DP]*4+[C.c_int,C.c_uint64,C.c_int,C.POINTER(C.c_int)]
        if not self.lib.robust_load(*(os.fsencode(p) for p in (value,opponent,response))):
            raise ValueError("Cannot load the baseline networks")
        if not self.lib.pressure_extras(os.fsencode(deal),vec(robust,12),vec(args.third_rival,6)):
            raise ValueError("Cannot load the recipient value network")
        self.pool=ThreadPoolExecutor(args.workers)

    def batch(self,weights,op,seed,n,mode=0):
        out=(C.c_int*4)()
        self.lib.pressure_batch((C.c_double*12)(*weights),*self.models,op,mode,seed,n,out)
        return list(out)

    def package(self,weights,rival,k):
        out=(C.c_int*5)()
        self.lib.pressure_bundle((C.c_double*12)(*weights),*self.models[:3],rival,
                                 stream("pressure-package-confirm",rival,k),20,out)
        return list(out)


def portfolio(weights,rates,minimum):
    return minimum*min(rates[:5])+(1-minimum)*sum(w*r for w,r in zip(weights,rates))


def direct(arena):
    def candidate(premium=1.50,deny=1.2,floor=0,management=1,offer=0,gate=1,block=0,rail=1,own=1):
        # Gate genes use zero during raw candidate evaluation to mark style 10.
        return [premium,deny,floor,management,offer,gate,block,rail,own,0,0,0]
    w=candidate
    pop=[CONTROL,w(),w(1.35,1.05),w(1.72,1.35,15),w(1.55,1.15,0,0),w(1.55,1.15,0,2),
         w(1.48,1.15,0,1,2),w(1.5,1.20,0,1,0,0),w(1.7,1.3,0,1,0,0),
         w(1.56,1.20,10,1,0,1,.12,1.1,1.1),w(1.78,1.,0,1,0,1,0,1.,1.25)]
    screen=arena.series(pop,[11,0,9,5,12,4],240,"direct-fit-develop-v1")
    def search_score(i):
        rates=[x[0]/240 for x in screen["outcomes"][i]]
        return portfolio([.22,.14,.14,.17,.18,.15],rates,.38)
    finalists=sorted(range(1,len(pop)),key=search_score,reverse=True)[:3]
    chosen=[CONTROL]+[pop[k] for k in finalists]
    selection=arena.series(chosen,[11,0,9,5,12,4,3,13],720,"direct-select-develop-v1")
    def final_score(i):
        rates=[v[0]/720 for v in selection["outcomes"][i]]
        return portfolio([.125]*8,rates,.40)-.50*max(0,selection["outcomes"][0][6][0]/720-rates[6]-.015)
    selected=chosen[max(range(1,len(chosen)),key=final_score)]
    confirmation=arena.series([CONTROL,selected],CONFIRM,1440,"direct-confirm-develop-v1")
    return [screen,selection,confirmation],selected


def gated(arena,raw):
    configs=[(1.2,.25,.25),(1.25,.60,.45),(1.30,.7,.53),(1.36,.35,.6),
             (1.15,.45,.45),(1.23,.90,.6),(1.30,.35,.15),(1.1,.2,.60)]
    pop=[CONTROL,raw]+[raw[:9]+list(p) for p in configs]
    screen=arena.series(pop,[11,0,9,5,12,4,13],360,"pressure-classify-v1")
    def score(i):
        rates=[v[0]/360 for v in screen["outcomes"][i]]
        return portfolio([.22,.16,.16,.16,.1,.1,.1],rates,.45)
    best=sorted(range(2,len(pop)),key=score,reverse=True)[:2]
    chosen=[CONTROL]+[pop[k] for k in best]
    check=arena.series(chosen,CONFIRM,1200,"pressure-classify-recheck-v1")
    return [screen,check],chosen[1]


def bundle_check(arena,weights):
    report=[]
    for w in weights:
        rows=[]
        for rival in range(4):
            jobs=[arena.pool.submit(arena.package,w,rival,k) for k in range(16)]
            rows.append([j.result() for j in jobs])
        report.append(rows)
    return dict(fixtures=["incumbent_v3","frozen_neural","blocker","cautious"],games_per_cell=320,
                columns=["wins","losses","turn_caps","package_offers","package_acceptances"],blocks=report)


def package(args,hashes,raw,chosen,phases,bundles):
    model=dict(format="monopoly.pressure.v1",parameter_order=PARAMETERS,weights=chosen)
    dump(args.output,model)
    counts=[p["games_per_cell"]*len(p["rivals"])*len(p["vectors"]) for p in phases]
    dump(args.report,dict(format="monopoly.pressure.training.v1",selected_model=model,
         opponents=OPPONENTS,input_sha256=hashes,stages=phases,bundle_confirmation=bundles,
         selection_games=counts[0]+counts[1]+counts[3],single_game_confirmation_games=counts[2]+counts[4],
         bundle_confirmation_games=320*4*len(bundles["blocks"]),
         method=("Full matches with a 1,000-turn cap, unresolved games count as nonwins. Historical "
                 "challengers are known sparring partners. The learned pressure policy quotes a scaled "
                 "public neural valuation and uses no challenger-specific parameter vector. Direct "
                 "and history-gated profiles share fitted bidding weights. The gate finalist with the top "
                 "screening rank is selected before its reported confirmation results.")))


def train(args):
    with tempfile.TemporaryDirectory(prefix="monopoly-pressure-") as tmp:
        arena=Arena(Path(tmp),args)
        phases,raw=direct(arena)
        tests,chosen=gated(arena,raw)
        bundles=bundle_check(arena,[CONTROL,raw,chosen])
        package(args,arena.hashes,raw,chosen,phases+tests,bundles)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-directory",type=Path,default=ROOT/"models")
    parser.add_argument("--output",type=Path,default=ROOT/"models/pressure.json")
    parser.add_argument("--report",type=Path,default=ROOT/"reports/pressure-training.json")
    for flag,file in (("first","qualification-rival"),("second","qualification-rival-v2"),("third","qualification-rival-v3")):
        parser.add_argument(f"--{flag}-rival",type=Path,default=ROOT/"reports"/(file+".json"))
    parser.add_argument("--workers",type=int,default=min(8,os.cpu_count() or 1))
    train(parser.parse_args())
