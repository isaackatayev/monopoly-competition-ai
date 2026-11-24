"""Replay the 2,000 release games with selected players' trade windows closed."""
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import native
from league_evaluate import stream
from rule_shift_bench import seeds

HARNESS = """
#include "evaluation/policy.hpp"
using namespace mono;
using namespace mono::evaluation;
extern "C" int ablation(EvalConfig cfg, uint64_t seed, int allowed, int cap, int* out) {
    const Policy ours=entrant(cfg), other=preset(1);
    for(int seat=0;seat<2;++seat) {
        Game game(seed,seat ? other : ours,seat ? ours : other);
        int allowed_seats = seat ? ((allowed&1)<<1) | ((allowed&2)>>1) : allowed;
        if (allowed!=3) {
            game.trade_window_context=&allowed_seats;
            game.trade_window=[](Game& g,int who) {
                if (*static_cast<int*>(g.trade_window_context) & (1<<who)) g.trade(who);
            };
        }
        int winner=game.play(cap);
        *out++=winner==seat; *out++=winner==1-seat; *out++=winner<0; *out++=game.s.turn;
        *out++=game.offers_made[seat]; *out++=game.offers_closed[seat];
        *out++=game.offers_made[1-seat]; *out++=game.offers_closed[1-seat];
    }
    return sizeof(EvalConfig);
}
"""
MODES=("no_trades","only_v4_proposes","only_collector_proposes","both_propose")
FIELDS=("wins","losses","caps","total_turns","offers_sent","outgoing_accepted","offers_received","incoming_accepted")


def difference(lhs,rhs):
    values=[(a-b)/2 for a,b in zip(lhs,rhs)]
    mean=statistics.mean(values)
    margin=1.96*statistics.stdev(values)/math.sqrt(len(values))
    return {"win_rate_change":mean,"approximate_paired_95_interval":[mean-margin,mean+margin]}


def main():
    release=json.loads((ROOT/"reports/league-v4.json").read_bytes())
    checkpoint=ROOT/"models/adaptive-v4.json"
    digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if digest!=release["manifest_sha256"]:
        raise ValueError("Reference checkpoint changed")
    player=native.model(checkpoint)
    cfg=native.config(player)
    start=stream(release["seed"],"collector","board")
    data=list(seeds(start,1000))
    cap=release["turn_cap"]
    with tempfile.TemporaryDirectory() as tmp:
        binary=Path(tmp)/"ablation.so"
        cpp=Path(tmp)/"ablation.cpp"
        cpp.write_text(HARNESS)
        compiler=shlex.split(os.environ.get("CXX","c++"))
        subprocess.run([*compiler,"-O2","-std=c++17","-shared","-fPIC","-I",str(ROOT/"src"),
                        str(cpp),str(ROOT/"build/libmonopoly.so"),"-o",str(binary)],check=True)
        lib=C.CDLL(str(binary))
        lib.ablation.argtypes=[native.Config,C.c_uint64,C.c_int,C.c_int,C.POINTER(C.c_int)]
        def run(bits):
            totals=dict.fromkeys(FIELDS,0)
            wins_by_board=[]
            for seed in data:
                ans=(C.c_int*(2*len(FIELDS)))()
                if lib.ablation(cfg,seed,bits,cap,ans)!=C.sizeof(cfg):
                    raise RuntimeError("Native configuration mismatch")
                for k,v in enumerate(FIELDS):
                    totals[v]+=ans[k]+ans[k+len(FIELDS)]
                wins_by_board.append(ans[0]+ans[len(FIELDS)])
            assert sum(totals[k] for k in FIELDS[:3])==len(data)*2
            assert bits&1 or totals["offers_sent"]==totals["outgoing_accepted"]==0
            assert bits&2 or totals["offers_received"]==totals["incoming_accepted"]==0
            return totals,wins_by_board
        with ThreadPoolExecutor(4) as pool:
            results=dict(zip(MODES,pool.map(run,range(len(MODES)))))
    reference=release["results"]["collector"]["candidate"]
    assert all(results["both_propose"][0][k]==reference[k] for k in FIELDS)
    summary={name:{**counts,"win_rate":counts["wins"]/(2*len(data))}
             for name,(counts,_) in results.items()}
    contrasts={}
    for i,lhs in enumerate(MODES):
        for rhs in MODES[:i]:
            contrasts[lhs+"_minus_"+rhs]=difference(results[lhs][1],results[rhs][1])
    report={"model":"adaptive-v4","checkpoint_sha256":digest,"rival":"collector","reference":"league-v4.json",
            "start_stream":start,"board_seeds":len(data),"games_per_condition":2*len(data),"individual_turn_cap":cap,
            "method":"Same 1,000 initial dice/deck seeds in both seat assignments under all four trade-window rules. An allowed proposer uses the full normal transaction path, including the original recipient decision. A suppressed proposer creates no offer or public offer event. The estimate includes subsequent adaptation to the changed game and trade history. Outcome is an outright win; a capped game is a non-win. Interval estimates use the variance across independent initial seeds after averaging both seats.",
            "control_verification":"Wins, losses, caps, total turns, offers sent and received and both acceptance counts matched the locked qualification.",
            "reproduce":"make all && python3 tests/collector_trade_ablation.py","results":summary,
            "paired_differences":contrasts}
    output=ROOT/"reports/collector-trade-ablation.json"
    output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(summary))
    print(json.dumps(contrasts))


if __name__=="__main__":
    main()
