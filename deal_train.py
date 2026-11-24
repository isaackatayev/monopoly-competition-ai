"""Fit a neural estimate of the recipient's gain from accepting a legal single-deed offer."""
import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import tempfile

for name in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","VECLIB_MAXIMUM_THREADS"):
    os.environ[name] = "2"
import numpy as np

ROOT = Path(__file__).resolve().parent
INPUTS, HIDDEN = 86, 32
SIZES = (("train",180000),("validation",20000),("test",20000))


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(args, work, value):
    jobs = [(split,shard,args.seed+10_000_019*i+100003*shard,size//8)
            for i,(split,size) in enumerate(SIZES) for shard in range(8)]

    def generate(job):
        split,shard,seed,size=job
        output=work/f"{split}-{shard}.f32"
        process=subprocess.run([ROOT/"build"/"deal_data",str(size),str(seed),"6",value,output],
                               capture_output=True,text=True,check=True)
        return split,size,int(process.stdout)
    counts={}
    with ThreadPoolExecutor(args.workers) as pool:
        for name,games,offers in pool.map(generate,jobs):
            split=counts.setdefault(name,{"boards":0,"offers":0})
            split["boards"]+=games
            split["offers"]+=offers
    return counts


def read_split(work,name):
    rows=np.vstack([np.fromfile(work/f"{name}-{i}.f32",dtype="<f4").reshape(-1,INPUTS+1) for i in range(8)])
    return rows[:,:INPUTS].copy(), rows[:,-1].copy()


def train(work):
    x,y=read_split(work,"train")
    val,vy=read_split(work,"validation")
    test,ty=read_split(work,"test")
    rng=np.random.default_rng(48193574)
    center=x.mean(0)
    scale=x.std(0).clip(.10)
    standardized=(x-center)/scale
    params=[rng.normal(0,.09,(INPUTS,HIDDEN)).astype("f"),np.zeros(HIDDEN,"f"),
            rng.normal(0,.025,HIDDEN).astype("f"),np.zeros(1,"f")]
    first=[np.zeros_like(p) for p in params]
    second=[p.copy() for p in first]
    step=0
    best_loss=float("inf")

    def predict(raw,p):
        weights,bias,out,intercept=p
        hidden=np.maximum(0,((raw-center)/scale)@weights+bias)
        return (hidden@out+intercept+raw[:,85]).clip(-1,1)

    for epoch in range(18):
        for idx in np.array_split(rng.permutation(len(x)),(len(x)+511)//512):
            weights,bias,out,intercept=params
            z=standardized[idx]@weights+bias
            h=np.maximum(0,z)
            error=(h@out+intercept+x[idx,85]-y[idx])*(2/len(idx))
            dz=error[:,None]*out*(z>0)
            grads=[standardized[idx].T@dz,dz.sum(0),h.T@error,error.sum(keepdims=True)]
            step+=1
            for i,grad in enumerate(grads):
                first[i]=.9*first[i]+.1*grad
                second[i]=.999*second[i]+.001*grad*grad
                params[i]-=.001*(first[i]/(1-.9**step))/(np.sqrt(second[i]/(1-.999**step))+1e-8)
        loss=float(((predict(val,params)-vy)**2).mean())
        if loss<best_loss:
            best_loss=loss
            best=[p.copy() for p in params]
    weights,bias,out,intercept=best
    # Fold training normalization into the first layer for the native reader.
    weights=weights/scale[:,None]
    bias=bias-center@weights
    blob=b"MDEAL001"+b"".join(p.astype("<f4").tobytes() for p in (weights,bias,out,intercept))
    pred=predict(test,best)
    material=abs(ty)>.12
    return blob,dict(test_mse=float(((pred-ty)**2).mean()),board_only_test_mse=float(((test[:,85]-ty)**2).mean()),
                     material_offer_accuracy=float(np.mean((pred[material]>0)==(ty[material]>0))),
                     board_only_accuracy=float(np.mean((test[material,85]>0)==(ty[material]>0))),
                     material_test_cases=int(material.sum()))


def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as tmp:
        tmp.write(data)
        stage=Path(tmp.name)
    stage.replace(path)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-directory",type=Path,default=ROOT/"models")
    parser.add_argument("--output",type=Path,default=ROOT/"models"/"deal.bin")
    parser.add_argument("--report",type=Path,default=ROOT/"reports"/"deal-training.json")
    parser.add_argument("--workers",type=int,default=6)
    parser.add_argument("--seed",type=int,default=559108106)
    args=parser.parse_args()
    if args.workers<1:
        parser.error("--workers must be positive")
    basepath=args.model_directory/"neural.json"
    base=json.loads(basepath.read_text())
    value=basepath.parent/base["value_network"]
    if checksum(value)!=base["sha256"]:
        raise ValueError("Board model checksum failed")
    subprocess.run(["make","build/deal_data"],cwd=ROOT,check=True)
    with tempfile.TemporaryDirectory(prefix="monopoly-deal-") as tmp:
        work=Path(tmp)
        counts=collect(args,work,value)
        blob,metrics=train(work)
    save(args.output,blob)
    report=dict(format="monopoly.deal-training.v1",seed=args.seed,optimizer_seed=48193574,
                splits=counts,paired_futures_per_offer=6,horizon_individual_turns=170,
                target="Mean recipient game-score difference between accepting and declining from the same public board. "
                       "Each pair uses the same sampled decks, dice seed and two continuation policies. "
                       "Future offers are suppressed. At the horizon an unfinished game uses its board-network value.",
                features="36 pre-deal offer features, 24 public economic features, their 24 changes, "
                         "and two board-network values (initial score and its change).",
                value_sha256=base["sha256"],sha256=hashlib.sha256(blob).hexdigest(),metrics=metrics)
    save(args.report,(json.dumps(report,indent=2)+"\n").encode())
    print(json.dumps(report["metrics"]),flush=True)
