"""Reproduce the antisymmetric Monopoly value MLP from fresh simulation outcomes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parent
GLOBAL, SEAT, WIDTH, H1, H2 = 4, 62, 128, 48, 24
SOURCES = ("opponent.cpp", "neural_data.cpp", "neural_value.cpp", "board.cpp", "economy.cpp", "holdout.cpp",
           "search.cpp", "policy.cpp")
PARAMS = ("acquisition", "completion", "denial", "railroads", "auction", "reserve", "development",
          "financing", "trade_demand", "trade_defense", "jail_patience", "house_scarcity")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def collect(work, legacy, workers, seed, sizes, cap):
    executable = work / "collect"
    command = [os.environ.get("CXX", "c++"), "-O3", "-std=c++17", "-Isrc"]
    command += [str(ROOT / "src" / s) for s in SOURCES] + ["-o", str(executable)]
    subprocess.run(command, cwd=ROOT, check=True)
    base = json.loads(Path(legacy).read_text())["weights"]
    weights = [str(base[k]) for k in PARAMS]
    data = {}
    for split_no, (name, games) in enumerate(sizes.items()):
        split_seed = seed + ((split_no + 1) << 40)
        def generate(i):
            a, b = games*i//workers, games*(i+1)//workers
            path = work / f"{name}-{i}.raw"
            out = subprocess.check_output([str(executable), str(path), str(b-a), str(a),
                                           str(split_seed), str(cap), *weights], text=True)
            return path, np.fromstring(out, dtype=np.int64, sep=" ")
        with ThreadPoolExecutor(workers) as pool:
            parts = list(pool.map(generate, range(workers)))
        total = np.sum([counts for _, counts in parts], axis=0)
        whole = work / f"{name}.raw"
        with whole.open("wb") as out:
            for path, _ in parts:
                with path.open("rb") as f:
                    shutil.copyfileobj(f, out, length=1048576)
                path.unlink()
        if int(total[0]) != games or whole.stat().st_size != 4 * (WIDTH+1) * games:
            raise ValueError("Unexpected data collection size")
        data[name] = dict(games=games, capped=int(total[1]), seat0_wins=int(total[2]),
                          seat1_wins=games-int(total[1]+total[2]), turns=int(total[3]),
                          split_seed=split_seed, data_sha256=sha256(whole))
        print(f"Collected {name}: {games:,} games, {total[1]:,} capped", flush=True)
    return data


def pair_features(x):
    n = len(x)
    views = np.empty((2*n, WIDTH), np.float32)
    views[:n] = x
    views[n:, :GLOBAL] = x[:, :GLOBAL]
    views[n:, GLOBAL:GLOBAL+SEAT] = x[:, GLOBAL+SEAT:]
    views[n:, GLOBAL+SEAT:] = x[:, GLOBAL:GLOBAL+SEAT]
    return views


def forward(weights, views):
    w1, b1, w2, b2, w3 = weights
    h1 = views @ w1
    np.maximum(h1 + b1, 0, out=h1)
    h2 = h1 @ w2
    np.maximum(h2 + b2, 0, out=h2)
    raw = h2 @ w3
    n = len(views)//2
    return raw[:n] - raw[n:], h1, h2


def logits(weights, data):
    result = np.empty(len(data), np.float32)
    for i in range(0, len(result), 2048):
        rows = data[i:i+2048]
        result[i:i+len(rows)] = forward(weights, pair_features(rows[:, :WIDTH]))[0]
    return result


def bce(z, y):
    return float(np.mean(np.logaddexp(0, z) - y*z, dtype=np.float64))


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -32, 32)))


def metrics(logit, labels, temperature):
    logit = logit / temperature
    p = sigmoid(logit)
    hard = labels != .5
    # Reliability bins aggregate the decisions for both seats from each independent game.
    probs = np.concatenate((p, 1-p))
    targets = np.concatenate((labels, 1-labels))
    which = np.minimum((probs*10).astype(np.int32), 9)
    bins = []
    ece = 0.
    for i in range(10):
        sel = which == i
        count = int(sel.sum())
        if count:
            predicted, observed = float(probs[sel].mean()), float(targets[sel].mean())
            ece += count * abs(predicted-observed) / len(probs)
            bins.append(dict(lower=i/10, upper=(i+1)/10, count=count, predicted=predicted, observed=observed))
    return dict(bce=bce(logit, labels), brier=float(np.mean((p-labels)**2)),
                decided_accuracy=float(np.mean((p[hard] >= .5) == labels[hard])) if np.any(hard) else None,
                calibration_error=ece, calibration_bins=bins)


def save_network(path, weights, temperature):
    body = bytearray(struct.pack("<8sIII", b"MNVNET01", WIDTH, H1, H2))
    for layer in [*weights, np.array([temperature], np.float32)]:
        body.extend(layer.astype("<f4").tobytes(order="C"))
    checksum = 14695981039346656037
    for byte in body:
        checksum = ((checksum ^ byte) * 1099511628211) & 0xffffffffffffffff
    body.extend(struct.pack("<Q", checksum))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def fit(args, work, stats):
    rng = np.random.default_rng(args.seed)
    datasets = {k: np.memmap(work / (k+".raw"), dtype=np.float32, mode="r",
                             shape=(meta["games"], WIDTH+1)) for k, meta in stats.items()}
    train, calibration, validation = [datasets[k] for k in ("training","calibration","validation")]
    def matrix(m, n):
        return rng.normal(0, (2/m)**.5, size=(m,n)).astype(np.float32)
    weights = [matrix(WIDTH,H1), np.zeros(H1,np.float32), matrix(H1,H2), np.zeros(H2,np.float32),
               rng.normal(0, .1, size=H2).astype(np.float32)]
    m = [np.zeros_like(w) for w in weights]
    v = [np.zeros_like(w) for w in weights]
    history, best_weights, best_loss = [], None, float("inf")
    step = 0
    for epoch in range(args.epochs):
        learning_rate = .0016 * (.16**(epoch/max(1,args.epochs-1)))
        shuffled = rng.permutation(len(train))
        for start in range(0,len(train),args.batch):
            batch = train[shuffled[start:start+args.batch]]
            x = pair_features(batch[:, :WIDTH])
            z, h1, h2 = forward(weights, x)
            dz = (sigmoid(z) - batch[:, WIDTH]) / len(batch)
            ds = np.concatenate((dz, -dz))
            dw3 = h2.T @ ds
            dh2 = (ds[:,None] * weights[4][None,:]) * (h2 > 0)
            dw2 = h1.T @ dh2
            db2 = dh2.sum(axis=0)
            dh1 = (dh2 @ weights[2].T) * (h1 > 0)
            dw1 = x.T @ dh1
            db1 = dh1.sum(axis=0)
            step += 1
            corrected = learning_rate * (1-.999**step)**.5 / (1-.9**step)
            for w, g, a, b in zip(weights, [dw1,db1,dw2,db2,dw3], m, v):
                a *= .9
                a += .1*g
                b *= .999
                b += .001*g*g
                w -= corrected * a / (np.sqrt(b) + 1e-8)
        loss = bce(logits(weights, calibration), calibration[:,WIDTH])
        history.append(dict(epoch=epoch+1, calibration_bce=loss, learning_rate=learning_rate))
        if loss < best_loss:
            best_loss, best_epoch = loss, epoch+1
            best_weights = [w.copy() for w in weights]
        print(f"Epoch {epoch+1}: calibration BCE {loss:.5f}", flush=True)
    calibration_logits = logits(best_weights, calibration)
    temperatures = np.linspace(.80,1.20,81)
    temperature = float(min(temperatures, key=lambda t: bce(calibration_logits/t, calibration[:,WIDTH])))
    val_logits = logits(best_weights, validation)
    val_labels = validation[:,WIDTH]
    report = dict(format="monopoly.value.training.v1", seed=args.seed, turn_cap=args.cap,
        behavior="Each seat independently samples collector, builder, cautious, untrained, frozen champion, or randomly perturbed champion weights. A separate 20% chance per seat disables trades. Seats are randomly swapped. The blocker and the neural policy are excluded.",
        observation="One uniformly reservoir-sampled live pre-turn snapshot per match. The shared MLP receives both seat perspectives. Bankruptcy labels are 1/0, capped matches are 0.5 for each seat.",
        architecture=dict(input=WIDTH, hidden=[H1,H2], nonlinearities="ReLU",
            output="sigmoid((score(player,opponent) - score(opponent,player)) / temperature)",
            parameters=int(sum(w.size for w in best_weights)), temperature=temperature),
        optimizer=dict(name="Adam", objective="binary cross-entropy with soft capped outcomes",
            batch_games=args.batch, epochs=args.epochs, updates=step, beta1=.9, beta2=.999,
            schedule="geometric epoch decay from 0.0016 to 0.000256", selected_epoch=best_epoch),
        games=stats, training_perspectives=2*len(train), history=history, validation=metrics(val_logits,val_labels,temperature),
        validation_constant_half=dict(bce=bce(np.zeros_like(val_logits),val_labels),
                                      brier=float(np.mean((val_labels-.5)**2))),
        checksum_legacy_policy=sha256(args.legacy), generator_sources={
            nm:sha256(ROOT/"src"/nm) for nm in (*SOURCES,"game.hpp","groups.hpp","neural_value.hpp")},
        trainer_sha256=sha256(Path(__file__)), numpy_version=np.__version__)
    report["validation_by_turn"] = {}
    turn = np.rint(validation[:,0] * 100)
    for name, mask in (("0_to_19", turn < 20),("20_to_59",(turn >= 20) & (turn < 60)),("60_plus",turn >=60)):
        if mask.any():
            report["validation_by_turn"][name] = dict(games=int(mask.sum()),
                bce=bce(val_logits[mask]/temperature,val_labels[mask]))
    save_network(args.out,best_weights,temperature)
    report["model_sha256"] = sha256(args.out)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    tmp = args.report.with_suffix(args.report.suffix+".tmp")
    tmp.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    tmp.replace(args.report)
    from neural_manifest import write_manifest
    write_manifest(args.out,args.legacy,args.out.with_suffix(".json"))
    print(f"Validation BCE {report['validation']['bce']:.5f}  Brier {report['validation']['brier']:.5f}  model {report['model_sha256']}", flush=True)


def options():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games",type=int,default=1000000)
    p.add_argument("--calibration-games",type=int,default=20000)
    p.add_argument("--validation-games",type=int,default=40000)
    p.add_argument("--seed",type=int,default=714335017)
    p.add_argument("--epochs",type=int,default=6)
    p.add_argument("--batch",type=int,default=2048)
    p.add_argument("--workers",type=int,default=min(os.cpu_count() or 1,8))
    p.add_argument("--cap",type=int,default=800)
    p.add_argument("--legacy",type=Path,default=ROOT/"models"/"champion.json")
    p.add_argument("--out",type=Path,default=ROOT/"models"/"neural.bin")
    p.add_argument("--report",type=Path,default=ROOT/"reports"/"neural-training.json")
    return p.parse_args()


if __name__ == "__main__":
    args = options()
    sizes = {"training":args.games,"calibration":args.calibration_games,"validation":args.validation_games}
    if min(*sizes.values(),args.workers,args.cap,args.epochs,args.batch) < 1 or max(sizes.values()) >= 2**40:
        raise SystemExit("Game counts, cap, batch, epochs and workers must be positive; each split needs fewer than 2^40 games")
    with tempfile.TemporaryDirectory(prefix="monopoly-neural-") as tmp:
        work = Path(tmp)
        stats = collect(work,args.legacy,args.workers,args.seed,sizes,args.cap)
        fit(args,work,stats)
