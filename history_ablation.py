"""Play the shipped adaptive entrant with and without access to public behavior histories."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile

from adaptive_evaluate import ROOT, WIDTH, digest, engine_digest, stream, summarize, model_inputs, read_known_rival, serialized_rival

FIXTURES=("trading_then_refusal","refusal_then_trading","reactive","independent_bidder","learned_rival","blocker")
CORE="board economy policy holdout search neural_value neural_policy neural_trade response opponent adaptive_policy robust_policy pressure_policy deal_value".split()


def delta_summary(groups):
    # Stratify the uncertainty estimate because average performance is defined on these named rivals.
    means, variances = [], []
    for rows in groups:
        rows=sorted(rows)
        pairs=[sum(int(r[3][0]==0)-int(r[2][0]==0) for r in rows[i:i+2])/2
               for i in range(0,len(rows),2)]
        means.append(statistics.mean(pairs))
        variances.append(statistics.variance(pairs)/len(pairs))
    gain=statistics.mean(means)
    radius=1.959963984540054*math.sqrt(sum(variances))/len(means)
    return dict(estimate=gain,interval_95=[gain-radius,gain+radius])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model",type=Path,default=ROOT/"models"/"adaptive-v1.json")
    p.add_argument("--rival-checkpoint",type=Path,default=None)
    p.add_argument("--report",type=Path,default=None)
    p.add_argument("--games",type=int,default=1000,help="Games per condition and opponent, split equally across seats")
    p.add_argument("--cap",type=int,default=1000)
    p.add_argument("--seed",type=int,default=853660278)
    p.add_argument("--workers",type=int,default=4)
    p.add_argument("--prior-challenger",type=Path,default=ROOT/"reports"/"qualification-rival.json")
    p.add_argument("--prior-report",type=Path,default=ROOT/"reports"/"adaptive-evaluation.json")
    args=p.parse_args()
    if args.games<4 or args.games%2 or args.cap<1 or args.workers<1: p.error("Use positive workers/cap and even game count >=4")
    manifest,files,serialized=model_inputs(args.model)
    v2=manifest["format"]=="monopoly.adaptive.v2"
    fixtures=FIXTURES+(("prior_challenger",) if v2 else ())
    args.rival_checkpoint=args.rival_checkpoint or ROOT/"reports"/("qualification-rival-v2.json" if v2 else "qualification-rival.json")
    args.report=args.report or ROOT/"reports"/("history-ablation-v2.json" if v2 else "history-ablation.json")
    rival=json.loads(args.rival_checkpoint.read_bytes())
    build_hash=engine_digest()
    if (rival["target_manifest_sha256"]!=digest(args.model) or rival["turn_cap"]!=args.cap or
            args.seed in (rival["seed"],129461997,983260741)):
        raise ValueError("Rival checkpoint or holdout seed mismatch")
    if rival["engine_sha256"]!=build_hash:
        if v2: raise ValueError("Fresh challenger trained against a different adaptive build")
        read_known_rival(args.rival_checkpoint,args.prior_report,args.cap)
    inputs={name:serialized for name in fixtures}
    inputs["learned_rival"]+=" "+serialized_rival(rival)
    if v2:
        known=read_known_rival(args.prior_challenger,args.prior_report,args.cap)
        if args.seed==known["seed"] or rival["seed"]==known["seed"]:
            raise ValueError("Require separately trained rivals and independent ablation seeds")
        inputs["prior_challenger"]+=" "+serialized_rival(known)
    driver=ROOT/"tests"/"history_ablation.cpp"
    groups={name:[] for name in fixtures}
    with tempfile.TemporaryDirectory(prefix="monopoly-history-ablation-") as tmp:
        binary=Path(tmp)/"ablation"
        sources=CORE+(["resilience"] if (ROOT/"src"/"resilience.cpp").exists() else [])
        subprocess.run([os.environ.get("CXX","c++"),"-O3","-std=c++17","-Isrc",str(driver),
                        *["src/"+name+".cpp" for name in sources],"-o",str(binary)],cwd=ROOT,check=True)
        executable_hash=digest(binary)
        def chunk(name,start,n):
            cmd=[str(binary),"learned_rival" if name=="prior_challenger" else name,str(stream(args.seed,name,"ablation-board")),str(start),str(n),
                 str(args.cap),*files,str(stream(args.seed,name,"ablation-regime"))]
            out=subprocess.run(cmd,input=inputs[name],text=True,capture_output=True,check=True).stdout
            rows=[]
            for line in out.splitlines():
                values=list(map(int,line.split()))
                if len(values)!=2+2*WIDTH:raise ValueError("Malformed game record")
                rows.append((values[0],values[1],values[2:2+WIDTH],values[2+WIDTH:]))
            if [(r[0],r[1]) for r in rows]!=[(i,seat) for i in range(start,start+n) for seat in (0,1)]:
                raise ValueError("Partial or unbalanced game block")
            return name,rows
        with ThreadPoolExecutor(args.workers) as pool:
            jobs=[pool.submit(chunk,name,start,min(25,args.games//2-start)) for start in range(0,args.games//2,25) for name in fixtures]
            for job in as_completed(jobs):
                name,rows=job.result()
                groups[name].extend(rows)
    if build_hash!=engine_digest():raise RuntimeError("Compared source changed while games were running")
    results={}
    for name,rows in groups.items():
        scored=summarize(rows)
        results[name]=dict(masked=scored["frozen"],with_history=scored["adaptive"],
                           delta=scored["win_rate_change"],paired_outcomes=scored["outcomes_frozen_to_adaptive"])
    report=dict(format="monopoly.history-ablation.v1",seed=args.seed,games_per_condition_per_fixture=args.games,turn_cap=args.cap,
        condition="Two complete games for each distinct board and seat, same frozen adaptive weights, policy and rival fixture. Only the masked entrant's view clears both public behavior tails on every decision. Its recurrent refusal/aggression estimates revert to 0.5 and its observed auction-price profile receives no events. The underlying engine and rival keep their histories. Current assets and the previous rejection terms from v1 remain available.",
        method="Identical starting dice/deck seeds and initial fixture schedules for each within-seat comparison; both seats per board seed. Later paths can diverge under different actions. Unresolved games count as non-wins. Per-fixture 95% intervals use the paired win-rate differences clustered by board seed. Aggregates weight fixtures equally and combine within-fixture variance estimates.",
        engine_sha256=build_hash,main_harness_sha256=digest(ROOT/"src"/"adaptive_bench.cpp"),
        driver_sha256=digest(driver),executable_sha256=executable_hash,manifest_sha256=digest(args.model),
        learned_rival_checkpoint_sha256=digest(args.rival_checkpoint),results=results,
        switches_only_delta=delta_summary([groups[name] for name in FIXTURES[:3]]),
        all_fixtures_delta=delta_summary(list(groups.values())))
    if v2:
        report["known_challenger_checkpoint_sha256"]=digest(args.prior_challenger)
    else:
        report["all_six_delta"]=report["all_fixtures_delta"]
    args.report.parent.mkdir(parents=True,exist_ok=True)
    pending=args.report.with_suffix(".pending");pending.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n");pending.replace(args.report)
    for name,r in results.items(): print(f"{name} {r['masked']['wins']} -> {r['with_history']['wins']} wins / {args.games}; delta {r['delta']['estimate']:+.2%}")
    print("Switches",report["switches_only_delta"],"All",report["all_fixtures_delta"])


if __name__=="__main__":main()
