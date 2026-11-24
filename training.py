"""Population search and seat-balanced, independently seeded evaluation."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

from native import ROOT, NAMES, batch, model, model_data, preset

TRAIN_SEED = 34519
EVAL_SEED = 93276104
DEFAULT_CAP = 1000
WORKERS = min(12, os.cpu_count() or 1)


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def derived(*parts):
    # Separate random streams by purpose; independent of scheduling order.
    raw = json.dumps(parts, separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "little")


def source_id():
    h = hashlib.sha256()
    for path in sorted(list((ROOT / "src").glob("*")) + [ROOT / "training.py"]):
        if path.is_file():
            h.update(path.name.encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def outcomes(stats):
    return {k: stats[k] for k in ("wins", "losses", "caps", "turns")}


def train(args):
    # Ten candidates, five opponents, both seats for every seed.
    divisor = args.generations * 10 * 5 * 2
    if args.games < divisor or args.games % divisor:
        raise ValueError(f"--games must be a positive multiple of {divisor} for balanced selection")
    per_cell = args.games // (args.generations * 50)
    champ = preset(0)
    center = list(champ[1])
    spread = [0.24] * len(center)
    archive = []
    history = []
    started = time.monotonic()
    identity = source_id()
    report_path = args.report
    with ThreadPoolExecutor(args.workers) as pool:
        for gen in range(args.generations):
            rng = random.Random(derived("parameters", args.seed, gen))
            pop = [champ]
            for i in range(1, 10):
                # Keep a broad exploration slot and explore locally around elite policies.
                if i == 9 and gen % 2 == 0:
                    w = [rng.random() for _ in center]
                else:
                    base = champ[1] if i <= 4 else center
                    scale = 0.65 if i <= 2 else 1.0
                    w = [max(0, min(1, rng.gauss(x, sigma*scale))) for x, sigma in zip(base, spread)]
                pop.append((0, w))
            historical = archive[(gen*7) % len(archive)] if archive else preset(0)
            opponents = [preset(1), preset(2), preset(3), champ, historical]
            tasks = []
            for i, candidate in enumerate(pop):
                for j, opponent in enumerate(opponents):
                    for chunk, offset in enumerate(range(0, per_cell, 100)):
                        size = min(100, per_cell-offset)
                        seed = derived("train", args.seed, gen, j, chunk)
                        future = pool.submit(batch, candidate, opponent, seed, size, args.cap, True)
                        tasks.append((i, j, future))
            scores = [[Counter() for _ in opponents] for _ in pop]
            for i, j, future in tasks:
                scores[i][j].update(future.result())

            def fitness(i):
                total = sum((row for row in scores[i]), Counter())
                return (total["wins"] + 0.5 * total["caps"]) / (5 * per_cell)

            order = sorted(range(len(pop)), key=fitness, reverse=True)
            previous = champ
            champ = pop[order[0]]
            if order[0] != 0 or not archive:
                archive.append(previous)
            elite = [pop[i][1] for i in order[:3]]
            for k in range(len(center)):
                mean = sum(w[k] for w in elite) / len(elite)
                variance = sum((w[k] - mean)**2 for w in elite) / len(elite)
                center[k] = 0.25 * center[k] + 0.75 * mean
                spread[k] = max(0.065, 0.55 * spread[k] + 0.45 * math.sqrt(variance))
            completed = (gen+1) * per_cell * 50
            totals = sum((row for candidate in scores for row in candidate), Counter())
            history.append(dict(generation=gen+1, games=per_cell*50, outcomes=outcomes(totals),
                                selected=order[0], candidates=[
                {"weights": candidate[1], "fitness": fitness(i),
                 "outcomes_by_opponent": [outcomes(row) for row in scores[i]]}
                for i, candidate in enumerate(pop)]))
            # The report and weights are individually replaced atomically.
            save(report_path, dict(method="population search with archived self-play", seed=args.seed,
                 source_sha256=identity, turn_cap=args.cap, completed_games=completed,
                 candidate_count=10, generations_planned=args.generations,
                 opponent_order=["collector", "builder", "cautious", "incumbent", "archive"],
                 parameter_order=NAMES, generations=history))
            save(args.output, dict(**model_data(champ[1]), trade_search="paired-rollouts-v1", training_games=completed,
                 training_seed=args.seed, turn_cap=args.cap, source_sha256=identity))
            print(f"Generation {gen+1}/{args.generations}   {completed:,} games   "
                  f"selection score {fitness(order[0]):.4f}   {time.monotonic()-started:.1f}s", flush=True)


        # Choose the deployment checkpoint on a separate set of common seeds.
        # Each candidate has to retain performance against each fixed opponent.
        opponents = [preset(1), preset(2), preset(3), champ]
        games_per_matchup = min(5000, max(100, 5 * per_cell))
        results = [[Counter() for _ in opponents] for _ in pop]
        pending = []
        for i, candidate in enumerate(pop):
            for j, opponent in enumerate(opponents):
                for offset in range(0, games_per_matchup, 100):
                    n = min(100, games_per_matchup-offset)
                    seed = derived("checkpoint", args.seed, j, offset)
                    pending.append((i, j, pool.submit(batch, candidate, opponent, seed, n, args.cap, True)))
        for i, j, future in pending:
            results[i][j].update(future.result())
        rates = [[cell["wins"]/games_per_matchup for cell in row] for row in results]
        incumbent = order[0]
        eligible = [i for i in range(len(pop)) if all(rates[i][j] >= rates[incumbent][j] - .01
                                                      for j in range(3))]
        selected = max(eligible, key=lambda i: sum(rates[i]))
        # Do not replace on a tie.
        if sum(rates[selected]) <= sum(rates[incumbent]):
            selected = incumbent
        validation_games = len(pop)*len(opponents)*games_per_matchup
        report = json.loads(Path(report_path).read_text())
        report["checkpoint_selection"] = dict(
            games=validation_games, seed_namespace="checkpoint", incumbent=incumbent,
            selected=selected, eligible=eligible, opponents=["collector","builder","cautious","incumbent"],
            outcomes=[[outcomes(cell) for cell in row] for row in results])
        save(report_path, report)
        save(args.output, dict(**model_data(pop[selected][1]), trade_search="paired-rollouts-v1",
             training_games=completed, validation_games=validation_games, training_seed=args.seed,
             turn_cap=args.cap, source_sha256=identity,
             selected_generation=args.generations, selected_candidate=selected))
        print(f"Checkpoint selection: {validation_games:,} validation games; "
              f"candidate {selected} selected (generation winner {incumbent}).", flush=True)


def wilson(wins, n):
    z = 1.959963984540054
    p = wins / n
    den = 1 + z*z/n
    mid = (p + z*z/(2*n)) / den
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return [max(0, mid-margin), min(1, mid+margin)]


def evaluate(args):
    if args.games < 2 or args.games % 2:
        raise ValueError("--games must be even and at least two per opponent")
    champ = model(args.model)
    results = {}
    with ThreadPoolExecutor(args.workers) as pool:
        for name in args.opponents:
            opponent = champ if name == "mirror" else model(name)
            jobs = [pool.submit(batch, champ, opponent, derived("holdout", args.seed, name, offset),
                                min(100, args.games-offset), args.cap, False)
                    for offset in range(0, args.games, 100)]
            s = sum((Counter(f.result()) for f in jobs), Counter())
            n = s["wins"]+s["losses"]+s["caps"]
            win_rate = s["wins"]/n
            results[name] = dict(**outcomes(s), games=n, win_rate=win_rate,
                                 first_seat_wins=s["first_wins"], first_seat_games=s["first_games"],
                                 second_seat_wins=s["wins"]-s["first_wins"],
                                 second_seat_games=n-s["first_games"], win_rate_95=wilson(s["wins"], n),
                                 trades={key: s[key] for key in ("offers_sent", "outgoing_accepted",
                                         "offers_received", "incoming_accepted")})
            print(f'{name:12} {s["wins"]:5} wins  {s["losses"]:5} losses  '
                  f'{s["caps"]:5} capped  {win_rate:7.2%} wins / all games', flush=True)
    report = dict(seed=args.seed, turn_cap=args.cap, independent_seeds=True,
                  interval="two-sided Wilson 95%, capped games counted as non-wins",
                  source_sha256=source_id(), model_name=args.model, model_style=champ[0],
                  prior_weights=dict(zip(NAMES, champ[1])),
                  network_sha256=champ[2].sha256 if champ[0]>=6 else None,
                  response_sha256=champ[3].sha256 if champ[0]>=6 else None,
                  opponent_sha256=champ[4].sha256 if champ[0]>=7 else None,
                  specialist_sha256=champ[5].sha256 if champ[0]>=7 else None,
                  bidding_model_sha256=champ[6].sha256 if champ[0]>=8 else None,
                  robust_sha256=champ[7].sha256 if champ[0]>=9 else None,
                  deal_sha256=champ[8].sha256 if champ[0]>=9 and champ[8] else None,
                  pressure_sha256=champ[9].sha256 if champ[0]>=10 else None, results=results)
    save(args.report, report)
