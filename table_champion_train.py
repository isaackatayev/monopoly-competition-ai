"""Fit the table Champion's existing purchase and liquidity weights on paired games."""
import ctypes as C
import json
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import native
from checkpoint_publish import digest, encoded, install, replace_file

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
BOARD = native.board_info()
STREETS = {g: [p["square"] for p in BOARD if p["group"] == g] for g in range(8)}
PARAMS = ("first_option", "opening_cash", "building_cash", "market_caution", "cash_pressure",
          "first_purchase_right", "working_cash", "housing_priority")


def evaluate(job):
    values, seed, pairs = job
    source = native.model("adaptive-v4")
    policy = (12, *source[1:])
    specialist = json.loads((MODELS / "specialist.json").read_text())["parameter_order"]
    robust = json.loads((MODELS / "robust.json").read_text())["parameter_order"]
    for name, value in zip(PARAMS, values):
        if name in specialist:
            policy[5].weights[specialist.index(name)] = value
        else:
            policy[7].weights[robust.index(name)] = value
    config = native.config(policy)
    rivals = [native.model(name) for name in ("adaptive-v4", "collector", "blocker")]
    rivals.append((12, *rivals[0][1:]))
    wins = draws = overpaid = purchases = short = threats = 0
    for pair in range(pairs):
        opponent = native.config(rivals[pair % len(rivals)])
        for seat in range(2):
            declined = set()
            @native.CALLBACK
            def act(snapshot, who, request, reply):
                nonlocal overpaid, purchases, short, threats
                s, q = snapshot.contents, request.contents
                native.lib.mono_decide(config, snapshot, who, request, reply)
                if declined:
                    for event in s.history[who][:s.history_size[who]]:
                        kind, square, amount, turn = event[:4]
                        if kind == 5 and (turn, square) in declined:
                            overpaid += max(0, amount - BOARD[square]["price"])
                            declined.remove((turn, square))
                if q.kind == 0:
                    purchases += 1
                    if not reply.contents.value:
                        declined.add((s.turn, q.square))
                if q.kind == 1 and q.amount >= 0:
                    group = STREETS.get(BOARD[q.square]["group"], ())
                    if group and all(s.owner[i] == 1-who for i in group if i != q.square):
                        threats += 1
                        short += s.cash[who] < BOARD[q.square]["price"]
                return 1
            turns = C.c_int()
            winner = native.lib.mono_external(opponent, seed + pair, 500, seat, act, C.byref(turns))
            if winner < -1:
                raise RuntimeError("Decision callback disconnected")
            wins += winner == seat
            draws += winner == -1
    return dict(games=2*pairs, wins=wins, draws=draws, buy_decisions=purchases,
                overpayment_dollars=overpaid, blocking_auctions=threats, short_of_face=short)


def score(row):
    return ((row["wins"] + .5*row["draws"])/row["games"]
            - row["overpayment_dollars"]/max(1,row["buy_decisions"])/35
            - .65*row["short_of_face"]/max(1,row["blocking_auctions"]))


def improves(old, new):
    return (new["overpayment_dollars"]/new["buy_decisions"] < old["overpayment_dollars"]/old["buy_decisions"]
            and new["short_of_face"]/new["blocking_auctions"] < old["short_of_face"]/old["blocking_auctions"])


def publish(weights):
    manifest = json.loads((MODELS / "adaptive-v4.json").read_text())
    files = {}
    updates = dict(zip(PARAMS, weights))
    for key in ("specialist", "robust"):
        name = manifest[key]
        checkpoint = json.loads((MODELS / name).read_text())
        for index, parameter in enumerate(checkpoint["parameter_order"]):
            if parameter in updates:
                checkpoint["weights"][index] = updates[parameter]
        files[name] = encoded(checkpoint)
        manifest[key + "_sha256"] = digest(files[name])
    store = MODELS / "table-training"
    store.mkdir(exist_ok=True)
    bundle = install(store, files)
    for key in ("specialist", "robust"):
        manifest[key] = str((bundle / manifest[key]).relative_to(MODELS))
    replace_file(MODELS / "table-champion.json", encoded(manifest))


def main():
    original = json.loads((MODELS / "specialist.json").read_text())
    order = original["parameter_order"]
    robust = json.loads((MODELS / "robust.json").read_text())
    initial = dict(zip(order, original["weights"]))
    initial.update(zip(robust["parameter_order"], robust["weights"]))
    baseline = [initial[p] for p in PARAMS]
    rng = random.Random(810351)
    candidates = [baseline] + [[rng.random() for _ in PARAMS] for _ in range(40)]
    with ProcessPoolExecutor(6) as pool:
        screen = list(pool.map(evaluate, [(v, 903240, 32) for v in candidates]))
        finalists = sorted(range(1,len(screen)), key=lambda i: score(screen[i]), reverse=True)[:8]
        validation = list(pool.map(evaluate, [(candidates[i], 634000, 96) for i in [0, *finalists]]))
        eligible = [(i, row) for i, row in zip(finalists, validation[1:]) if improves(validation[0], row)]
        if not eligible:
            raise RuntimeError("No candidate improved both validation metrics")
        best = max(eligible, key=lambda pair: score(pair[1]))[0]
        test = list(pool.map(evaluate, [(v, 787001, 512) for v in (baseline,candidates[best])]))
    report = dict(parameters=PARAMS, candidates=candidates, screening=screen, finalists=finalists,
                  validation=validation, selected=best, held_out=test)
    report.update(opponents=["adaptive-v4", "collector", "blocker", "original table Champion"],
                  seed_ranges={"screen": [903240, 903271], "validation": [634000, 634095],
                               "held_out": [787001, 787512]}, validation_order=[0, *finalists])
    replace_file(ROOT / "reports" / "table-champion-training.json", encoded(report))
    print(test, flush=True)
    if not improves(*test):
        raise RuntimeError("Held-out behavior failed to improve on both measures")
    publish(candidates[best])


if __name__ == "__main__":
    main()
