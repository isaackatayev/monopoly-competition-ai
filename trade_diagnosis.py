"""Measure the incumbent's offer-selection weakness using paired development boards."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import subprocess

from adaptive_evaluate import ROOT, digest, interval, model_inputs, serialized_rival

FIXTURES = ("prior_challenger_v1", "prior_challenger_v2", "collector", "builder", "cautious", "legacy", "frozen_neural")
MODES = ("unchanged", "no_outgoing", "no_rival_color_completion")


def run(games,seed,workers,fixtures,output):
    source=ROOT/"tests"/"trade_diagnosis.cpp"
    binary=ROOT/"build"/"trade_diagnosis"
    # Prerequisite list matches the shipping C++ rules and policy core.
    build=subprocess.run(["make","-s","--no-print-directory","-f","-","print_flags"],cwd=ROOT,
                         input="include Makefile\nprint_flags:\n\t@echo $(CXX) $(CXXFLAGS) $(CORE)\n",
                         text=True,capture_output=True,check=True)
    subprocess.run([*shlex.split(build.stdout),str(source),"-o",str(binary)],cwd=ROOT,check=True)
    _,files,baseline=model_inputs(ROOT/"models"/"adaptive-v1.json")
    def batch(job):
        f,mode,i,g=job
        weights=baseline
        opponent=f
        if f.startswith("prior_challenger"):
            opponent="learned_rival"
            path=ROOT/"reports"/("qualification-rival.json" if f.endswith("v1") else "qualification-rival-v2.json")
            weights+=" "+serialized_rival(json.loads(path.read_bytes()))
        cmd=[str(binary),opponent,str(g),str(mode),str(seed),str(i//2),*files]
        p=subprocess.run(cmd,text=True,input=weights,capture_output=True,check=True)
        results=[tuple(map(int,line.split())) for line in p.stdout.splitlines()]
        if [(r[0],r[1]) for r in results] != [(p,s) for p in range(i//2,(i+g)//2) for s in (0,1)]:
            raise ValueError("Incomplete or mismatched seat pair")
        return f,mode,results
    jobs=[(f,m,i,min(80,games-i)) for i in range(0,games,80) for f in fixtures for m in range(len(MODES))]
    rows=defaultdict(list)
    with ThreadPoolExecutor(workers) as pool:
        for f,m,data in pool.map(batch,jobs):
            rows[f,m].extend(data)
    report={"format":"monopoly.trade-diagnosis.v1","seed":seed,"games_per_condition":games,"turn_cap":1000,
            "source_sha256":digest(source),"binary_sha256":digest(binary),"manifest_sha256":digest(ROOT/"models"/"adaptive-v1.json"),
            "method":"Each board seed is played in each seat. All three conditions use identical boards, rules, and incumbent v1 policy. Only its final outgoing offer may be suppressed. Ties reaching 1,000 turns are nonwins. Intervals compare outright wins within each independently sampled seat pair. Individual intervals do not adjust for multiple fixtures.",
            "filter":"Suppress a proposed one-deed or cash exchange when its outgoing deed would grant the rival a new full color group. Applies even if both gain a set. Incoming offers and all other decisions use the incumbent.",
            "results":{}}
    for f in fixtures:
        base=sorted(rows[f,0]);out={}
        for m,name in enumerate(MODES):
            rr=sorted(rows[f,m]);d=[]
            for i in range(0,len(rr),2):
                d.append(sum(int(rr[j][2]==0)-int(base[j][2]==0) for j in (i,i+1))/2)
            out[name]={"wins":sum(r[2]==0 for r in rr),"losses":sum(r[2]==1 for r in rr),"caps":sum(r[2]==2 for r in rr),
                       "offers":sum(r[3] for r in rr),"accepted":sum(r[4] for r in rr),"change":interval(d)}
        report["results"][f]=out
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix(".pending")
    temp.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n");temp.replace(output)
    for f,r in report["results"].items():
        print(f, *(r[k]["wins"] for k in MODES), r[MODES[2]]["change"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games",type=int,default=1600)
    p.add_argument("--seed",type=int,default=40928041)
    p.add_argument("--workers",type=int,default=4)
    p.add_argument("--opponents",nargs="+",choices=FIXTURES,default=FIXTURES)
    p.add_argument("--report",type=Path,default=ROOT/"reports"/"trade-diagnosis.json")
    args=p.parse_args()
    if args.games<2 or args.games%2 or args.workers<1:p.error("Expected even positive games and positive workers")
    run(args.games,args.seed,args.workers,args.opponents,args.report)
