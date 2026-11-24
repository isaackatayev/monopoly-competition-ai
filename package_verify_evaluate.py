"""Measure the optional package verifier while holding both scalar players fixed."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess

from adaptive_evaluate import ROOT, digest, interval
from league_evaluate import entrant_inputs

FIXTURES = {"adaptive_v1":7, "collector":1, "blocker":4}
METHODS = ("original","verified")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games",type=int,default=1400)
    parser.add_argument("--seed",type=int,default=60182197)
    parser.add_argument("--workers",type=int,default=min(6,os.cpu_count() or 1))
    parser.add_argument("--report",type=Path,default=ROOT/"reports"/"package-verification.json")
    args=parser.parse_args()
    if args.games<2 or args.games%2 or args.workers<1:parser.error("Use even positive games and positive workers")
    entrant, baseline = ROOT/"models"/"adaptive-v3.json",ROOT/"models"/"adaptive-v1.json"
    files,weights,_=entrant_inputs(baseline,entrant)
    if len(files)!=4:raise ValueError("Expected all archived v3 networks")
    binary=ROOT/"build"/"package_verify"
    flags=subprocess.run(["make","-s","--no-print-directory","-f","-","print_flags"],cwd=ROOT,
                         input="include Makefile\nprint_flags:\n\t@echo $(CXX) $(CXXFLAGS) $(CORE)\n",
                         text=True,capture_output=True,check=True)
    subprocess.run([*shlex.split(flags.stdout),"-Isrc","src/bundles/verified.cpp","tests/package_verify.cpp",
                    "-o",str(binary)],cwd=ROOT,check=True)
    def batch(job):
        fixture,first,count=job
        cmd=[str(binary),str(FIXTURES[fixture]),str(first),str(count),str(args.seed),*files]
        proc=subprocess.run(cmd,input=weights.replace(" v3 "," "),text=True,capture_output=True,check=True)
        result=[list(map(int,line.split())) for line in proc.stdout.splitlines()]
        if [x[:3] for x in result]!=[[i,s,m] for i in range(first,first+count) for s in (0,1) for m in (0,1)]:
            raise ValueError("Missing paired games or duplicate terminal rows")
        return fixture,result
    rows={f:[] for f in FIXTURES}
    pairs=args.games//2
    jobs=[(f,i,min(10,pairs-i)) for i in range(0,pairs,10) for f in FIXTURES]
    with ThreadPoolExecutor(args.workers) as pool:
        for f,rr in pool.map(batch,jobs):
            rows[f].extend(rr)
    results={}
    for f,rr in rows.items():
        parts={};arms=[]
        for m,name in enumerate(METHODS):
            arm=sorted(r for r in rr if r[2]==m)
            arms.append(arm)
            keys=("turns","scalar_offers","scalar_closed","package_offers","package_closed",
                  "rival_scalar_offers","rival_scalar_closed","rival_package_offers","rival_package_closed",
                  "multi_deed_closed_both_seats","held_card_closed_both_seats")
            parts[name]={**{k:sum(r[i] for r in arm) for i,k in enumerate(keys,4)},
                         "wins":sum(r[3]==0 for r in arm),"losses":sum(r[3]==1 for r in arm),
                         "caps":sum(r[3]==2 for r in arm)}
        differences=[sum(int(arms[1][j][3]==0)-int(arms[0][j][3]==0) for j in (i,i+1))/2
                     for i in range(0,args.games,2)]
        parts["win_rate_change"]=interval(differences);results[f]=parts
    sources=[ROOT/"src"/"bundle_trade.cpp",ROOT/"src"/"bundle_match.cpp",ROOT/"src"/"bundles"/"verified.cpp",
             ROOT/"tests"/"package_verify.cpp",Path(__file__)]
    report={"format":"monopoly.package-verification.v1","seed":args.seed,"games_per_method_opponent":args.games,
            "turn_cap":1000,"model_sha256":digest(entrant),"v1_sha256":digest(baseline),"binary_sha256":digest(binary),
            "source_sha256":{str(p.relative_to(ROOT)):digest(p) for p in sources},
            "method":"Compare original and verified package negotiation on the exact v3 scalar entrant. Each initial shuffled board is played with both methods in both seats against the same opponent. Adaptive v1 negotiates genuine packages; collector uses a fixed 0.55 asset-based acceptance threshold; blocker declines them. Games capped at 1,000 turns are nonwins. Approximate 95% intervals use within-seed seat-pair differences. Package counts are attributed to the issuing seat and include multi-deed or held-card deals.",
            "selection":"A separate 240-game adaptive-v1 development screen (seed 7180789) tested controlling outgoing offers and incoming confirmations. The shorter proposer verifier with selective scalar fallback and a conservative recipient verifier was fixed before the fresh three-opponent comparison.",
            "results":results}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.report.with_suffix(".pending");tmp.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    tmp.replace(args.report)
    for f,parts in results.items():
        print(f,parts["original"]["wins"],parts["verified"]["wins"],parts["win_rate_change"])


if __name__=="__main__":
    main()
