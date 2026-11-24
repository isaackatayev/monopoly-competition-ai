"""Compare a locked multiplayer checkpoint to the frozen duel-trained player on fresh boards."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import statistics

import four_player
import multiplayer_model
import native
from training import save


FIXTURES = {
    3: {
        "collectors": ("collector", "collector"),
        "mixed_basic": ("builder", "cautious"),
        "archived_neural": ("adaptive-v1", "adaptive-v3"),
        "blocker_and_basic": ("blocker", "collector"),
        "incumbents": ("adaptive-v4", "adaptive-v4"),
        "blocker_and_neural": ("blocker", "adaptive-v3"),
    },
    4: {
        "collectors": ("collector", "collector", "collector"),
        "mixed_basic": ("collector", "builder", "cautious"),
        "archived_neural": ("adaptive-v1", "adaptive-v2", "adaptive-v3"),
        "blocker_and_basic": ("blocker", "collector", "builder"),
        "incumbents": ("adaptive-v4", "adaptive-v4", "adaptive-v4"),
        "blocker_and_neural": ("blocker", "adaptive-v3", "frozen"),
    },
}
OUTCOMES = ("wins", "losses", "caps")
TRADE_COUNTS = ("outgoing_proposed", "outgoing_accepted", "incoming_proposed", "incoming_accepted")


def interval(estimate, variance):
    radius = 1.96 * math.sqrt(variance)
    return [max(-1., estimate - radius), min(1., estimate + radius)]


def qualify(args):
    if args.candidate.resolve() == args.report.resolve():
        raise ValueError("Use separate paths for the checkpoint and qualification report")
    if args.boards < 2 or args.workers < 1 or args.cap < 1:
        raise ValueError("Use at least two boards per lineup, one worker and one turn")
    digest = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    entrant, overlay = multiplayer_model.load(str(args.candidate), args.seats)
    if overlay is None:
        raise ValueError("Expected a multiplayer checkpoint")
    engine = four_player.load_engine()
    fixtures = FIXTURES[args.seats]
    rivals = {name: native.model(name) for name in sorted({n for roster in fixtures.values() for n in roster})}
    reference_totals, entrant_totals = Counter(), Counter()
    variances = []
    results = {}
    for fixture, opponents in fixtures.items():
        # Use a separate stream per lineup even when seats and root seed match.
        key = f"monopoly/multiplayer/qualification/v1/{args.seed}/{args.seats}/{fixture}".encode()
        root = int.from_bytes(hashlib.sha256(key).digest()[:8], "little")
        lineup = [entrant, *(rivals[name] for name in opponents)]
        configs = [native.config(model) for model in lineup]
        weights = [overlay, *([None] * len(opponents))]

        def play(index):
            seed = four_player.board_seed(root, index, args.seats)
            match = (engine, configs, opponents, index, seed, args.cap, True)
            return four_player.play_board(*match), four_player.play_board(*match, weights=weights)

        with ThreadPoolExecutor(args.workers) as pool:
            paired = list(pool.map(play, range(args.boards)))
        old, new = Counter(), Counter()
        deltas = []
        for before, after in paired:
            old.update(before)
            new.update(after)
            deltas.append((after["wins"] - before["wins"]) / args.seats)
        games = args.boards * args.seats
        diff = statistics.mean(deltas)
        variance = statistics.variance(deltas) / args.boards
        variances.append(variance)
        reference_totals.update(old)
        entrant_totals.update(new)
        results[fixture] = {
            "opponents": opponents, "games_per_entrant": games,
            "reference": {k: old[k] for k in (*OUTCOMES, *TRADE_COUNTS)},
            "candidate": {k: new[k] for k in (*OUTCOMES, *TRADE_COUNTS)},
            "reference_accepted_outgoing_types": {k: old[k] for k in four_player.TRADE_TYPES},
            "candidate_accepted_outgoing_types": {k: new[k] for k in four_player.TRADE_TYPES},
            "reference_wins_by_seat": [old[f"seat_{i}"] for i in range(args.seats)],
            "candidate_wins_by_seat": [new[f"seat_{i}"] for i in range(args.seats)],
            "reference_rate": old["wins"] / games, "candidate_rate": new["wins"] / games,
            "paired_delta": diff, "paired_delta_95_interval": interval(diff, variance),
        }
        print(f"{args.seats} seats, {fixture}: {old['wins']}/{games} to {new['wins']}/{games}", flush=True)
    if hashlib.sha256(args.candidate.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Candidate changed during qualification")
    games = args.seats * args.boards * len(fixtures)
    diff = (entrant_totals["wins"] - reference_totals["wins"]) / games
    ci = interval(diff, sum(variances) / len(fixtures) ** 2)
    report = {
        "seats": args.seats, "seed": args.seed, "turn_cap": args.cap,
        "boards_per_fixture": args.boards, "games_per_entrant": games,
        "candidate_sha256": digest, "base": multiplayer_model.BASE,
        "reference_policy": four_player.checkpoint_info(multiplayer_model.BASE, entrant),
        "opponent_policies": {name: four_player.checkpoint_info(name, policy) for name, policy in rivals.items()},
        "engine_sha256": hashlib.sha256((native.ROOT / four_player.TARGET).read_bytes()).hexdigest(),
        "candidate": {k: entrant_totals[k] for k in OUTCOMES},
        "reference": {k: reference_totals[k] for k in OUTCOMES},
        "win_rate": entrant_totals["wins"] / games, "reference_win_rate": reference_totals["wins"] / games,
        "paired_delta": diff, "paired_delta_95_interval": ci, "beats_reference": ci[0] > 0,
        "fixtures": results,
        "method": ("Both policies played the same six equally weighted lineups, independently seeded "
                   "within each lineup. Each board was played from every seat, with trading enabled. "
                   "Only outright wins count. The two-sided approximate 95% interval is based on the "
                   "variance of the paired difference across board clusters within each lineup. "
                   "Selection and training use separate board-seed namespaces."),
    }
    save(args.report, report)
    print(json.dumps({k: report[k] for k in ("win_rate", "reference_win_rate", "paired_delta_95_interval")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seats", required=True, type=int, choices=(3, 4))
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--boards", type=int, default=600, help="Independent starts per lineup")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1124968031)
    qualify(parser.parse_args())
