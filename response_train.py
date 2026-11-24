"""Train a model of whether a simulated recipient accepts a particular legal deal."""
import os
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "2"
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import numpy as np

ROOT = Path(__file__).resolve().parent
D, H = 36, 24


def data(folder, split, games, seed):
    path = Path(folder)/split
    out = subprocess.check_output([str(ROOT/"build"/"response_data"), str(games), str(seed), str(path)], text=True)
    games, rows, accepts, caps = map(int, out.split())
    ds = np.fromfile(path, dtype="<f4").reshape(-1,D+1)
    assert len(ds)==rows and ds[:,-1].sum()==accepts
    return ds[:,:D], ds[:,D:], dict(games=games, legal_offers=rows, accepted=accepts, caps=caps, seed=seed)


def forward(x, weights):
    w, b, v, c = weights
    h = np.maximum(x @ w + b, 0)
    z = np.clip(h @ v + c, -30, 30)
    return h, 1/(1+np.exp(-z))


def loss(y, yhat):
    yhat = np.clip(np.asarray(yhat,dtype=np.float64),1e-8,1-1e-8)
    return float(-(y*np.log(yhat)+(1-y)*np.log1p(-yhat)).mean())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games",type=int,default=50000)
    parser.add_argument("--epochs",type=int,default=12)
    parser.add_argument("--seed",type=int,default=51751)
    parser.add_argument("--output",type=Path,default=ROOT/"models"/"response.bin")
    parser.add_argument("--report",type=Path,default=ROOT/"reports"/"response-training.json")
    args=parser.parse_args()
    if args.games<100 or args.epochs<1: parser.error("Use at least 100 games and one epoch")
    subprocess.run(["make","build/response_data"],cwd=ROOT,check=True)
    with tempfile.TemporaryDirectory() as folder:
        x,y,train=data(folder,"train",args.games,args.seed)
        vx,vy,validation=data(folder,"validation",max(500,args.games//10),args.seed+1)
        tx,ty,test=data(folder,"test",max(500,args.games//10),args.seed+2)
    rng=np.random.default_rng(args.seed)
    weights=[rng.normal(0,(2/D)**.5,(D,H)).astype("f4"),np.zeros(H,"f4"),
             rng.normal(0,.1,(H,1)).astype("f4"),np.zeros(1,"f4")]
    m=[np.zeros_like(w) for w in weights]
    v=[np.zeros_like(w) for w in weights]
    steps=0
    best_loss=float("inf")
    epochs=[]
    for e in range(args.epochs):
        order=rng.permutation(len(x))
        for k in range(0,len(x),1024):
            ids=order[k:k+1024]
            xb=x[ids]
            h,p=forward(xb,weights)
            out=(p-y[ids])/len(ids)
            dh=(out @ weights[2].T)*(h>0)
            grads=[xb.T @ dh,dh.sum(axis=0),h.T @ out,out.sum(axis=0)]
            steps+=1
            for j,g in enumerate(grads):
                m[j]=.9*m[j]+.1*g
                v[j]=.999*v[j]+.001*g*g
                rate=.002*(1-.8*e/args.epochs)
                weights[j]-=rate*(m[j]/(1-.9**steps))/(np.sqrt(v[j]/(1-.999**steps))+1e-8)
        score=loss(vy,forward(vx,weights)[1])
        epochs.append(score)
        if score<best_loss:
            best_loss=score
            best=[w.copy() for w in weights]
    pred=forward(tx,best)[1]
    blob=b"MRESP001"+b"".join(w.astype("<f4").tobytes() for w in best)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.output.with_suffix(".pending")
    temporary.write_bytes(blob)
    temporary.replace(args.output)
    report=dict(architecture=[D,H,1], activation="ReLU and sigmoid", parameters=sum(w.size for w in best),
                target="recipient accepted a delivered legal offer", train=train, validation=validation,
                test=test, optimizer="Adam, binary cross entropy", epochs=args.epochs,
                validation_log_loss=epochs, test_log_loss=loss(ty,pred),
                test_constant_log_loss=loss(ty,float(y.mean())), test_brier=float(((pred-ty)**2).mean()),
                test_accuracy=float(((pred>=.5)==ty).mean()), sha256=hashlib.sha256(blob).hexdigest())
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n")
    print(f"Fit {len(y):,} labeled offers; test log loss {report['test_log_loss']:.4f} vs constant {report['test_constant_log_loss']:.4f}.")
    if args.output.resolve() == ROOT/"models"/"response.bin" and (ROOT/"models"/"neural.bin").exists():
        from neural_manifest import write_manifest
        write_manifest(ROOT/"models"/"neural.bin",ROOT/"models"/"champion.json",ROOT/"models"/"neural.json")


if __name__=="__main__":
    main()
