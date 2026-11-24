"""Fit a full-table decision network from wins in three- or four-player Monopoly."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time

import four_player
import multiplayer_model
import native
from training import derived, save

BASE = multiplayer_model.BASE
ZERO = (0.0,) * multiplayer_model.PARAMS
FORMAT = "monopoly.multiplayer-training.v1"
ROOT = native.ROOT
# A fixture gets the same weight in the objective regardless of match duration.
ROSTERS = {
    3: [
        ("collector", "collector"), ("builder", "cautious"),
        ("adaptive-v1", "adaptive-v3"), ("blocker", "@variation"),
        ("@incumbent", "collector"), ("@field", "@field"),
    ],
    4: [
        ("collector", "collector", "collector"), ("collector", "builder", "cautious"),
        ("adaptive-v1", "adaptive-v2", "adaptive-v3"), ("blocker", "builder", "@variation"),
        ("@incumbent", "collector", "@field"), ("@field", "@field", "@field"),
    ],
}
FIELD = ("collector", "builder", "cautious", "blocker", "adaptive-v1",
         "adaptive-v2", "adaptive-v3", "@variation")
TRAITS = (1.0,) * 16 + (.35,) * 7 + (.65,)


def stream(seed, seats, purpose, *keys):
    return derived("multiplayer-network-fit-v1", seed, seats, purpose, *keys)


def clipped(x):
    return round(max(-4.0, min(4.0, x)), 7)


def mutate(parent, scale, rng):
    return tuple(clipped(p + scale * strength * rng.gauss(0.0, 1.0))
                 for p, strength in zip(parent, TRAITS))


def profile(bid, trade, adjustments=()):
    vector = list(ZERO)
    vector[0], vector[8] = bid, trade
    for index, value in adjustments:
        vector[index] = value
    return tuple(vector)


GROUP_VALUE = ((1, 1.0), (2, .75), (3, .35), (4, .3), (6, -.5))
TRADE_VALUE = ((9, 2.0), (10, -1.65), (11, .35), (12, .20), (13, -.3), (14, -.4), (15, .4))
STARTS = (
    profile(-.45, 0), profile(0, -.40), profile(.40, 0), profile(0, .35),
    profile(-1.3, 0), profile(1.4, 0), profile(0, -1.4),
    profile(.65, -.10, GROUP_VALUE + TRADE_VALUE),
    profile(1.5, -.10, GROUP_VALUE + TRADE_VALUE + ((6, -1.15),)),
    profile(1.2, -.65), profile(2.4, -1.4), profile(1.65, 0, GROUP_VALUE),
    profile(.65, .6), profile(-.6, -.8), profile(0, -1.7),
)


def isolated(vector, head):
    if head == "acquisition":
        return (*vector[:8], *ZERO[8:16], *vector[16:])
    if head == "negotiation":
        return (*ZERO[:8], *vector[8:])
    return vector


def population(size, generation, parents, rng, warm=None):
    candidates = [ZERO]

    def add(vector):
        if vector is not None and vector not in candidates and len(candidates) < size:
            candidates.append(vector)

    add(warm if generation == 0 else parents[0])
    if generation == 0:
        for w in STARTS[:max(1, min(len(STARTS), size // 2))]:
            add(w)
    elif len(parents) > 1:
        # Crossover in weight space is useful for keeping fit readouts across rounds.
        add(tuple(clipped(statistics.mean(v)) for v in zip(*parents)))
    if generation and size >= 10:
        # Retry low-dimensional adjustments of either sign each round. A zero
        # readout keeps that entire decision family on the original v4 behavior.
        for sign in (-1, 1):
            add(profile(clipped(sign * rng.uniform(.08, 1.5)), 0))
            add(profile(0, clipped(sign * rng.uniform(.08, 1.5))))
    # Later restarts keep exploring after an early elite stops improving.
    mutation = 0
    modes = ("acquisition", "negotiation", "both", "both")
    while len(candidates) < size:
        mode = modes[mutation % len(modes)]
        mutation += 1
        if generation == 0:
            center = rng.choice(([warm] if warm is not None else []) + list(STARTS))
            scale = rng.uniform(.45, 1.25)
        elif rng.random() < .18:
            center = rng.choice(STARTS)
            scale = rng.uniform(.65, 1.4)
        else:
            center = rng.choice(parents) if rng.random() < .7 else candidates[1]
            scale = rng.uniform(.3, 1.0) * max(.40, .91 ** generation)
        add(isolated(mutate(center, scale, rng), mode))
    return candidates


@dataclass
class Board:
    fixture: int
    index: int
    seed: int
    rivals: tuple
    weights: tuple


def schedule(seats, seed, phase, stage, boards, incumbent):
    plans = []
    rosters = ROSTERS[seats]
    # Rotate the slightly oversampled fixtures when boards is not a multiple of six.
    offset = stream(seed, seats, phase, stage, "fixture-offset") % len(rosters)
    for i in range(boards):
        fixture = (i + offset) % len(rosters)
        cycle = i // len(rosters)
        root = stream(seed, seats, phase, stage, fixture, cycle)
        rng = random.Random(derived(root, "opponents"))
        names, overlays = [], []
        for name in rosters[fixture]:
            if name == "@field":
                name = rng.choice(FIELD)
            if name == "@incumbent":
                names.append(BASE)
                overlays.append(incumbent if incumbent != ZERO else None)
            elif name == "@variation":
                names.append(BASE)
                overlays.append(mutate(rng.choice(STARTS), rng.uniform(.25, 1.1), rng))
            else:
                names.append(name)
                overlays.append(None)
        order_offset = stream(seed, seats, phase, stage, fixture, "order")
        plans.append(Board(fixture, cycle + order_offset, root, tuple(names), tuple(overlays)))
    return plans


class Arena:
    def __init__(self, seats, cap, workers):
        self.seats = seats
        self.cap = cap
        self.engine = four_player.load_engine()
        # Native config values contain borrowed pointers into these model tuples.
        self.models = {name: native.model(name) for name in (set(FIELD) - {"@variation"}) | {BASE}}
        self.configs = {name: native.config(model) for name, model in self.models.items()}
        self.pool = ThreadPoolExecutor(workers)

    def evaluate(self, vectors, plans):
        def play(job):
            weights, plan = job
            configs = [self.configs[BASE], *(self.configs[name] for name in plan.rivals)]
            overlays = [weights if weights != ZERO else None, *plan.weights]
            r = four_player.play_board(self.engine, configs, plan.rivals, plan.index, plan.seed,
                                       self.cap, True, overlays)
            return r["wins"], r["losses"], r["caps"]

        jobs = ((vector, board) for vector in vectors for board in plans)
        outcomes = iter(self.pool.map(play, jobs))
        records = []
        paired_baseline = None
        control_rates = None
        for vector in vectors:
            counts = [[0, 0, 0] for _ in ROSTERS[self.seats]]
            wins = []
            for board in plans:
                outcome = next(outcomes)
                for key, value in enumerate(outcome):
                    counts[board.fixture][key] += value
                wins.append(outcome[0])
            rates = [v[0] / sum(v) for v in counts]
            if paired_baseline is None:
                if vector != ZERO:
                    raise ValueError("The frozen control must be evaluated first")
                paired_baseline = wins
                control_rates = rates
            paired = [(w - c) / self.seats for w, c in zip(wins, paired_baseline)]
            records.append({
                "weights": list(vector), "fixture_wins_losses_caps": counts,
                "fixture_mean_win_rate": statistics.mean(rates),
                "wins": sum(wins), "games": len(plans) * self.seats,
                "paired_win_rate_difference": statistics.mean(paired),
                "fixture_mean_paired_win_rate_difference": statistics.mean(r-c for r,c in zip(rates,control_rates)),
            })
        return records


def rank_key(row):
    # Among exactly tied win rates, keep the smallest change from the frozen policy.
    return (row["fixture_mean_win_rate"], -sum(x * x for x in row["weights"]))


def finalists(stages, count, warm):
    # Preserve the last elite, then admit high paired performers from all rounds.
    pool = []
    if warm is not None and warm != ZERO:
        pool.append(warm)
    last_best = max(stages[-1]["candidates"][1:], key=rank_key)
    last = tuple(last_best["weights"])
    if last not in pool:
        pool.insert(0, last)
    candidates = (c for s in stages for c in s["candidates"][1:])
    for c in sorted(candidates, key=lambda v: (v["fixture_mean_paired_win_rate_difference"], rank_key(v)),
                    reverse=True):
        w = tuple(c["weights"])
        if w not in pool:
            pool.append(w)
        if len(pool) >= count:
            break
    return [ZERO, *pool[:count]]


def positive(raw):
    n = int(raw)
    if n < 1:
        raise argparse.ArgumentTypeError("Use a positive integer")
    return n


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seats", required=True, type=int, choices=(3, 4))
    parser.add_argument("--workers", type=positive, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--rounds", type=positive, default=5)
    parser.add_argument("--population", type=positive, default=16)
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--boards", type=positive, help="Starts per candidate per search round; default 36")
    budget.add_argument("--games", type=positive, help="Minimum total search games; rounds up to balanced blocks")
    parser.add_argument("--validation-boards", type=positive, default=192)
    parser.add_argument("--finalists", type=positive, default=5)
    parser.add_argument("--cap", type=positive, default=1000)
    parser.add_argument("--seed", type=int, default=217903553)
    parser.add_argument("--initial", type=Path, help="Include a multiplayer checkpoint in the search")
    parser.add_argument("--resume", action="store_true", help="Continue an identical run from its last full round")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    field_count = len(ROSTERS[args.seats])
    if args.games is not None:
        args.boards = max(field_count, math.ceil(args.games / (args.rounds * args.population * args.seats)))
    if args.boards is None:
        args.boards = 36
    if args.population < 4 or min(args.boards, args.validation_boards) < field_count:
        parser.error("Use at least 4 policies and enough boards to include each of the six fixtures")
    if not args.output:
        args.output = ROOT / "build" / "multiplayer-training" / f"candidate-{args.seats}.json"
    if not args.report:
        args.report = ROOT / "reports" / f"multiplayer-training-{args.seats}.json"
    if args.output.resolve() == args.report.resolve():
        parser.error("Choose separate paths for the checkpoint and the training report")
    return args


def train(args):
    start = time.monotonic()
    if args.initial:
        _, values = multiplayer_model.load(str(args.initial), args.seats)
        if values is None:
            raise ValueError("The warm start must be a multiplayer checkpoint")
        warm = tuple(values)
    else:
        warm = None
    arena = Arena(args.seats, args.cap, args.workers)
    try:
        settings = {key: getattr(args, key) for key in ("seats", "seed", "rounds", "population", "boards",
                                                         "cap", "validation_boards", "finalists")}
        settings["initial_weights"] = list(warm) if warm is not None else None
        dependencies = [Path(__file__), Path(four_player.__file__), Path(multiplayer_model.__file__),
                        native.ROOT / four_player.TARGET, multiplayer_model.BASE_PATH]
        checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dependencies}
        profiles = {name: four_player.checkpoint_info(name, mod) for name, mod in arena.models.items()}
        report = {
            "format": FORMAT, "settings": settings, "input_sha256": checksums, "opponents": profiles,
            "fixtures": ROSTERS[args.seats], "field_pool": FIELD, "rounds_completed": [],
            "method": ("Evolutionary training of 24 full-table neural network parameters on complete games. "
                       "All candidates share each round's boards, rival policies and rotated seats. "
                       "The six fixtures receive equal weight. The field draws fresh rival types and full-table "
                       "networks. The incumbent sparring partner freezes the previous round's best policy. "
                       "Only outright wins improve the objective. Search, validation and external qualification "
                       "use separate boards. Validation chooses a trained challenger for external testing."),
        }
        if args.resume:
            saved = json.loads(args.report.read_text())
            if (saved.get("format") != FORMAT or saved.get("settings") != settings
                    or saved.get("input_sha256") != checksums or saved.get("opponents") != profiles):
                raise ValueError("The run settings, opponents or simulator changed since the saved training run")
            report = saved
        stages = report["rounds_completed"]
        for generation in range(len(stages), args.rounds):
            previous = sorted(stages[-1]["candidates"], key=rank_key, reverse=True) if stages else []
            elites = [tuple(r["weights"]) for r in previous[:max(2, args.population // 4)]]
            incumbent = elites[0] if elites else warm or ZERO
            rng = random.Random(stream(args.seed, args.seats, "mutation", generation))
            vectors = population(args.population, generation, elites, rng, warm)
            plans = schedule(args.seats, args.seed, "search", generation, args.boards, incumbent)
            results = arena.evaluate(vectors, plans)
            best = max(results, key=rank_key)
            stages.append({"round": generation + 1, "sparring_weights": list(incumbent), "candidates": results})
            report["search_games"] = sum(sum(c["games"] for c in s["candidates"]) for s in stages)
            save(args.report, report)
            print(f"{args.seats} seats, round {generation + 1}/{args.rounds}, "
                  f"{report['search_games']:,} search games, best {best['fixture_mean_win_rate']:.2%}, "
                  f"control {results[0]['fixture_mean_win_rate']:.2%}, elapsed {time.monotonic()-start:.1f}s",
                  flush=True)
        if "validation" not in report:
            choices = finalists(stages, args.finalists, warm)
            sparring = tuple(max(stages[-1]["candidates"], key=rank_key)["weights"])
            plans = schedule(args.seats, args.seed, "validation", 0, args.validation_boards, sparring)
            outcomes = arena.evaluate(choices, plans)
            report["validation"] = {"candidates": outcomes, "sparring_weights": list(sparring)}
            report["validation_games"] = sum(row["games"] for row in outcomes)
            report["games"] = report["search_games"] + report["validation_games"]
            chosen = max(outcomes[1:], key=rank_key)
            report["selected"] = chosen
            save(args.report, report)
        chosen = report["selected"]
        save(args.output, multiplayer_model.candidate(args.seats, chosen["weights"]))
        print(f"Wrote {args.output}. Validation {chosen['fixture_mean_win_rate']:.2%}, frozen control "
              f"{report['validation']['candidates'][0]['fixture_mean_win_rate']:.2%}. "
              f"{report['games']:,} total games.", flush=True)
        return report
    finally:
        arena.pool.shutdown()


if __name__ == "__main__":
    train(options())
