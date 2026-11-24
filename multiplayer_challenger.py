"""Train a rival against one locked multiplayer bot, then test both the bot and v4 against it."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

import four_player
import multiplayer_model
import multiplayer_train as training
from training import derived, save

FILLERS = {
    3: [("collector",), ("builder",), ("cautious",), ("blocker",), ("adaptive-v1",), ("adaptive-v3",)],
    4: [("collector", "collector"), ("builder", "cautious"), ("collector", "blocker"),
        ("blocker", "adaptive-v1"), ("adaptive-v2", "adaptive-v3"), ("adaptive-v1", "collector")],
}
FORMAT = "monopoly.multiplayer-challenger.v1"


def seed_for(args, phase, *keys):
    return derived(FORMAT, args.seed, args.seats, phase, *keys)


def boards_for(budget, block_games, seats):
    fixtures = len(FILLERS[seats])
    return fixtures * math.ceil(budget / (fixtures * block_games))


def schedule(args, phase, round_index, count, opponent):
    plans = []
    for i in range(count):
        fixture = i % len(FILLERS[args.seats])
        cycle = i // len(FILLERS[args.seats])
        fillers = FILLERS[args.seats][fixture]
        order = cycle + seed_for(args, phase, round_index, fixture, "seating")
        seed = seed_for(args, phase, round_index, fixture, cycle, "board")
        plans.append(training.Board(fixture, order, seed, (training.BASE, *fillers),
                                    (opponent, *(None for _ in fillers))))
    return plans


def train_challenger(arena, args, locked):
    rounds = []
    starts = boards_for(args.search_games, args.seats * args.population * args.rounds, args.seats)
    for r in range(args.rounds):
        previous = sorted(rounds[-1]["candidates"], key=training.rank_key, reverse=True) if rounds else []
        parents = [tuple(row["weights"]) for row in previous[:max(2, args.population // 4)]]
        rng = random.Random(seed_for(args, "mutations", r))
        slate = training.population(args.population, r, parents, rng)
        outcomes = arena.evaluate(slate, schedule(args, "search", r, starts, locked))
        rounds.append({"candidates": outcomes})
    choices = training.finalists(rounds, 3, None)
    count = boards_for(args.selection_games, args.seats * len(choices), args.seats)
    selection = arena.evaluate(choices, schedule(args, "selection", 0, count, locked))
    chosen = max(selection[1:], key=training.rank_key)
    return tuple(chosen["weights"]), {
        "search_games": starts * args.seats * args.population * args.rounds,
        "selection_games": count * args.seats * len(choices),
        "round_best_rival_wins": [max(c["wins"] for c in r["candidates"]) for r in rounds],
        "search_games_per_rival_per_round": starts * args.seats,
        "selection_games_per_rival": count * args.seats,
        "selection_frozen_v4_rival_wins": selection[0]["wins"],
        "selection_trained_rival_wins": [r["wins"] for r in selection[1:]],
        "selected_rival_wins": chosen["wins"],
    }


def test_rival(arena, args, rival, locked):
    starts = boards_for(args.test_games, args.seats, args.seats)
    plans = schedule(args, "holdout", 0, starts, rival)
    counts = {"locked_candidate": Counter(), "original_v4": Counter()}
    by_fixture = defaultdict(list)

    def compare(board):
        configs = [arena.configs[training.BASE], *(arena.configs[n] for n in board.rivals)]
        labels = ["trained_challenger", *board.rivals[1:]]
        rows = []
        for target in (locked, None):
            result = four_player.play_board(arena.engine, configs, labels, board.index, board.seed,
                                            args.cap, True, [target, *board.weights])
            rival_wins = result["winner_trained_challenger"]
            rows.append({"target_wins": result["wins"], "rival_wins": rival_wins,
                         "other_wins": result["losses"] - rival_wins, "caps": result["caps"]})
        return board.fixture, rows

    for fixture, (candidate, baseline) in arena.pool.map(compare, plans):
        assert sum(candidate.values()) == sum(baseline.values()) == args.seats
        counts["locked_candidate"].update(candidate)
        counts["original_v4"].update(baseline)
        by_fixture[fixture].append((candidate["target_wins"] - baseline["target_wins"]) / args.seats)
    games = starts * args.seats
    for row in counts.values():
        row["win_rate"] = row["target_wins"] / games
    # Pair target changes on the same dice/deck seed, keeping all seat rotations in one cluster.
    # The equal-weight fixture mixture is fixed in advance.
    delta = statistics.mean(statistics.mean(values) for values in by_fixture.values())
    interval = None
    if all(len(v) >= 2 for v in by_fixture.values()):
        variance = sum(statistics.variance(v) / len(v) for v in by_fixture.values()) / len(by_fixture) ** 2
        radius = 1.96 * math.sqrt(variance)
        interval = [max(-1.0, delta - radius), min(1.0, delta + radius)]
    return {"games_per_target": games, "independent_starts": starts, "outcomes": counts,
            "paired_delta": delta, "paired_delta_95_interval": interval}


def run(args):
    if args.candidate.resolve() == args.report.resolve():
        raise ValueError("Use distinct checkpoint and report paths")
    digest = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    _, target = multiplayer_model.load(str(args.candidate), args.seats)
    if target is None:
        raise ValueError("Supply a trained three- or four-player checkpoint")
    arena = training.Arena(args.seats, args.cap, args.workers)
    try:
        rival, search = train_challenger(arena, args, tuple(target))
        held_out = test_rival(arena, args, rival, tuple(target))
    finally:
        arena.pool.shutdown()
    if digest != hashlib.sha256(args.candidate.read_bytes()).hexdigest():
        raise RuntimeError("The target checkpoint changed during the challenge")
    receipt = {
        "format": FORMAT, "seats": args.seats, "seed": args.seed, "turn_cap": args.cap,
        "rounds": args.rounds, "population": args.population,
        "locked_target_sha256": digest, "rival": multiplayer_model.candidate(args.seats, rival),
        "engine_sha256": hashlib.sha256((training.ROOT / four_player.TARGET).read_bytes()).hexdigest(),
        "background_lineups": FILLERS[args.seats], "training": search, "holdout": held_out,
        "design": ("The rival learns from its own outright wins at tables containing the locked candidate. "
                   "Each starting board is played from every seat. Search and selection use distinct seed "
                   "domains. Three trained rivals advance to selection where possible. The winning trained "
                   "challenger freezes before new boards are played with each target. The interval uses "
                   "paired win-rate differences across independent starts within each background lineup. "
                   "Games reaching the turn cap count as non-wins for every seat."),
    }
    save(args.report, receipt)
    print(json.dumps({"training": search, "holdout": held_out}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--seats", required=True, type=int, choices=(3, 4))
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=935170219)
    parser.add_argument("--workers", type=training.positive, default=4)
    parser.add_argument("--cap", type=training.positive, default=1000)
    parser.add_argument("--rounds", type=training.positive, default=4)
    parser.add_argument("--population", type=training.positive, default=10)
    parser.add_argument("--search-games", type=training.positive, default=6000)
    parser.add_argument("--selection-games", type=training.positive, default=864)
    parser.add_argument("--test-games", type=training.positive, default=1200)
    options = parser.parse_args()
    if options.population < 4:
        parser.error("Use at least four candidates per round")
    run(options)
