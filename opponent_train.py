"""Train a gated recurrent observer of a rival's public decisions under strategy switches."""
import os
for name in ("OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(name, "2")
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parent
D, H, T, META = 16, 12, 16, 6
BASE_SEED = 822257552113


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -25, 25)))


def read_rows(path):
    return np.fromfile(path, dtype=np.float32).reshape(-1,D+META)


def windows(rows, ends):
    indices = np.maximum(ends[:,None] - np.arange(T-1,-1,-1), 0)
    valid = (rows[indices,D] == rows[ends,None,D]) & (np.arange(T)[None,:]+ends[:,None]>=T-1)
    x = rows[indices,:D].copy()
    x *= valid[:,:,None]
    first = np.argmax(valid, axis=1)
    x[np.arange(len(ends)),first,13] = 0
    return x, valid


def forward(w, x, valid, train=False):
    ix, rec, bias, out, intercept = w
    b = len(x)
    project = (x.reshape(-1,D) @ ix + bias).reshape(b,T,3*H)
    h = np.zeros((b,H),np.float32)
    saved = []
    for i in range(T):
        hidden = h @ rec
        combined = project[:,i,:2*H] + hidden[:,:2*H]
        z,r = np.split(sigmoid(combined),2,axis=1)
        hn = hidden[:,2*H:]
        n = np.tanh(project[:,i,2*H:] + r*hn)
        # A refusal of an obviously poor offer does not update hidden beliefs.
        evidence = (np.where(x[:,i,0] != 0,x[:,i,7],1)*valid[:,i])[:,None]
        if train:
            saved.append((h,z,r,n,hn,evidence))
        h = h + evidence*(1-z)*(n-h)
    return sigmoid(h @ out + intercept), h, saved


def gradients(weights,x,valid,y):
    pred,h,history = forward(weights,x,valid,True)
    ds = (pred-y)/(2*len(y))
    dout = h.T @ ds
    dh = ds @ weights[3].T
    drec = np.zeros_like(weights[1])
    input_grad = np.empty((len(x),T,3*H),np.float32)
    for t in reversed(range(T)):
        old,z,r,n,hn,e = history[t]
        dn = dh*e*(1-z)*(1-n*n)
        dz = dh*e*(old-n)*z*(1-z)
        dr = dn*hn*r*(1-r)
        gi = np.concatenate((dz,dr,dn),axis=1)
        gh = np.concatenate((dz,dr,dn*r),axis=1)
        drec += old.T @ gh
        input_grad[:,t] = gi
        dh = dh*(1-e*(1-z)) + gh @ weights[1].T
    flat=input_grad.reshape(-1,3*H)
    # The output intercept is fixed at zero: a cold start and all-uninformative history stay neutral.
    return [x.reshape(-1,D).T @ flat,drec,flat.sum(axis=0),dout,np.zeros(2,np.float32)]


def predicts(w, rows, ids):
    answer=np.empty((len(ids),2),np.float32)
    for i in range(0,len(ids),1024):
        subset=ids[i:i+1024]
        x,valid=windows(rows,subset)
        answer[i:i+len(subset)]=forward(w,x,valid)[0]
    return answer


def bce(y,p):
    q = np.clip(p.astype(np.float64),1.e-7,1.-1.e-7)
    return float(np.mean(-y*np.log(q)-(1-y)*np.log1p(-q)))


def metric(y,p):
    return dict(log_loss=bce(y,p),brier=float(np.mean((p-y)**2)),
                accuracy=float(np.mean((p>=.5)==y)))


def examples(rows, rng, per_trajectory):
    boundaries=np.r_[0,np.where(np.diff(rows[:,D])!=0)[0]+1,len(rows)]
    selected=[]
    for a,b in zip(boundaries[:-1],boundaries[1:]):
        if b-a<=per_trajectory:
            selected.append(np.arange(a,b))
        else:
            # Include a short history and sample remaining endpoints throughout the game.
            selected.append(np.r_[a,rng.choice(np.arange(a+1,b),per_trajectory-1,replace=False)])
    return np.concatenate(selected)


def report_recovery(w,rows,ids,probs,axis):
    y = rows[ids,D+1+axis]
    age = rows[ids,D+3+axis].astype(np.int64)
    post = age>0
    if not np.any(post):
        return {}
    old = np.maximum(0,ids-age)
    has_old = (rows[old,D] == rows[ids,D]) & (ids>=age)
    prior = np.full(len(ids),.5,np.float32)
    needed=np.flatnonzero(post & has_old)
    if len(needed):
        unique,inv=np.unique(old[needed],return_inverse=True)
        prior[needed]=predicts(w,rows,unique)[inv,axis]
    buckets={}
    for name,mask in (("1",age==1),("2_to_3",(age>=2)&(age<=3)),
                      ("4_to_7",(age>=4)&(age<=7)),("8_to_15",(age>=8)&(age<=15)),("16_plus",age>=16)):
        if np.any(mask):
            score = metric(y[mask],probs[mask,axis])
            buckets[name]=dict(observations=int(mask.sum()),trajectories=int(np.unique(rows[ids[mask],D]).size),
                               updated=score, frozen_at_switch=metric(y[mask],prior[mask]))
    return buckets


def train(args, folder, counts):
    trainrows,cal,test=[read_rows(folder/(k+".raw")) for k in ("train","calibration","test")]
    rng=np.random.default_rng(args.seed)
    w=[rng.normal(0,.18,size=(D,3*H)).astype("f4"),
       rng.normal(0,.13,size=(H,3*H)).astype("f4"),np.zeros(3*H,"f4"),
       rng.normal(0,.16,size=(H,2)).astype("f4"),np.zeros(2,"f4")]
    m=[np.zeros_like(a) for a in w];v=[np.zeros_like(a) for a in w]
    cid=examples(cal,rng,10)
    best_loss=float("inf");best=None;history=[];step=0
    for epoch in range(args.epochs):
        train_ids=examples(trainrows,rng,10);rng.shuffle(train_ids)
        for i in range(0,len(train_ids),512):
            ids=train_ids[i:i+512];x,valid=windows(trainrows,ids)
            grad=gradients(w,x,valid,trainrows[ids,D+1:D+3]);step+=1
            rate=.003*(.32**(epoch/max(1,args.epochs-1)))*(1-.999**step)**.5/(1-.9**step)
            for a,g,u,b in zip(w,grad,m,v):
                u*=.9;u+=.1*g;b*=.999;b+=.001*g*g
                a-=rate*u/(np.sqrt(b)+1.e-8)
        p=predicts(w,cal,cid);loss=bce(cal[cid,D+1:D+3],p);history.append(loss)
        if loss<best_loss:best_loss=loss;best=[a.copy() for a in w]
        print(f"Opponent epoch {epoch+1}  calibration log loss {loss:.4f}",flush=True)
    ids=examples(test,rng,32);prob=predicts(best,test,ids)
    metrics={}
    for axis,name in enumerate(("unconditional_trade_refusal","high_acquisition_willingness")):
        y=test[ids,D+1+axis]
        target=trainrows[:,D+1+axis]
        metrics[name]=dict(overall=metric(y,prob[:,axis]),
                           training_prevalence_baseline=metric(y,np.full(len(y),target.mean(),np.float32)),
                           since_behavior_switch=report_recovery(best,test,ids,prob,axis))
    # Complement the empirical recovery curves with a controlled repeated-poor-offer sanity check.
    x=np.zeros((1,T,D),np.float32); mask=np.zeros((1,T),bool)
    mask[:,-6:]=True;x[:,-6:,0]=1;x[:,-6:,6]=-2;x[:,-6:,8]=-.5;x[:,-6:,15]=1
    poor=float(forward(best,x,mask)[0][0,0])
    x[:,-6:,6]=1;x[:,-6:,7]=sigmoid(np.array((220-20)/65))
    good=float(forward(best,x,mask)[0][0,0])
    x[:,-2:,0]=0;x[:,-2:,1]=1;x[:,-2:,7]=0
    reverse=float(forward(best,x,mask)[0][0,0])
    blob=b"MOPPGR01"+b"".join(a.astype("<f4").tobytes() for a in best)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.output.with_suffix(".tmp");tmp.write_bytes(blob);tmp.replace(args.output)
    report=dict(format="monopoly.opponent.gru.v1",seed=args.seed,
                architecture=dict(inputs=D,hidden=H,window=T,heads=2,trained_parameters=sum(a.size for a in best)-2,
                                  serialized_float32=sum(a.size for a in best),fixed_zero_output_intercepts=2,
                                  cell="GRU, reset-after, with a public offer-quality evidence gate"),
                observations="Only the opponent's last 16 public trade proposals/replies, face-price acquisitions/refusals, and paid deed auction prices. Inputs exclude bid ceilings, policy identity, switch dates, and outcomes.",
                labels="The first head estimates an unconditional trade-refusal regime. The second estimates the high-acquisition-willingness regime. These are simulator behavioral profiles; the heads do not identify an arbitrary program.",
                schedule="The profiled seat is randomized. Its refusal and purchase regimes switch independently with probabilities 0.75 and 2/3 at a randomly chosen turn from 8 through 87. The other seat proposes independently varied legal terms. Four strategic policy families are sampled; the separately implemented blocker is excluded.",
                dataset=counts,test_observations_scored=len(ids),optimizer="Adam / two-head binary cross entropy with full backpropagation through the last 16 events",
                updates=step,calibration_log_loss=history,test=metrics,
                controlled_refusal_probabilities=dict(cold=.5,six_obviously_poor_offers_declined=poor,
                    six_favorable_offers_declined=good,four_favorable_declines_then_two_accepts=reverse),
                sha256=hashlib.sha256(blob).hexdigest(),numpy_version=np.__version__)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.report.with_suffix(".tmp")
    tmp.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n");tmp.replace(args.report)
    return report


def get_args():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games",type=int,default=24000)
    ap.add_argument("--calibration-games",type=int,default=3500)
    ap.add_argument("--test-games",type=int,default=6000)
    ap.add_argument("--epochs",type=int,default=6)
    ap.add_argument("--seed",type=int,default=BASE_SEED)
    ap.add_argument("--output",type=Path,default=ROOT/"models"/"opponent.bin")
    ap.add_argument("--report",type=Path,default=ROOT/"reports"/"opponent-training.json")
    return ap.parse_args()


if __name__=="__main__":
    args=get_args()
    sizes=(args.games,args.calibration_games,args.test_games)
    if min(*sizes,args.epochs)<1 or max(sizes)>=1000000:
        raise SystemExit("All splits need 1 to 999,999 games and at least one training epoch")
    subprocess.run(["make","build/opponent_data"],cwd=ROOT,check=True)
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)
        def generate(spec):
            index,name,n=spec
            seed=args.seed+index*1000000
            out=subprocess.check_output([str(ROOT/"build"/"opponent_data"),str(n),str(seed),
                                         str(folder/(name+".raw"))],text=True)
            games,events,switches,caps=map(int,out.split())
            return name,dict(seed=seed,games=games,public_events=events,
                             games_with_observed_trade_switch=switches,capped=caps)
        with ThreadPoolExecutor(3) as executor:
            counts=dict(executor.map(generate,zip(range(3),("train","calibration","test"),sizes)))
        train(args,folder,counts)
