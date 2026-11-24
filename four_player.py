"""Run a seat-balanced tournament using the local three- and four-player simulator."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import statistics
import subprocess

import native
from training import save

SEATS = 4
TARGET = "build/libmonopoly_four.so"
OPPONENT_ORDERS = {seats: tuple(itertools.permutations(range(1, seats))) for seats in (3, 4)}
TRADE_TYPES = ("buy_deed_for_cash", "sell_deed_for_cash", "swap_deeds", "other")
TRADE_MATRIX = (C.c_int * SEATS) * SEATS
WEIGHT_COUNT = 24
WEIGHT_PTR = C.POINTER(C.c_double)


class Result(C.Structure):
    _fields_ = [
        ("winner", C.c_int), ("turns", C.c_int), ("survivors", C.c_int),
        ("offers", TRADE_MATRIX), ("accepted", TRADE_MATRIX),
        ("accepted_types", (C.c_int * len(TRADE_TYPES)) * SEATS),
    ]


def configure(parser, positive, seats=4):
    parser.add_argument("--model", default="champion")
    parser.add_argument("--opponents", nargs=seats-1, default=["collector"] * (seats-1), metavar="BOT")
    parser.add_argument("--boards", type=positive, default=600,
                        help="Independent starts, each played with the entrant in every seat")
    parser.add_argument("--cap", type=positive, default=1000, help="Individual turns per game")
    parser.add_argument("--seed", type=int, default=832716409)
    parser.add_argument("--no-trades", action="store_true")
    parser.add_argument("--workers", type=positive, default=min(6, os.cpu_count() or 1))
    parser.add_argument("--report", type=Path)


def load_engine():
    subprocess.run(["make", "-s", TARGET], cwd=native.ROOT, check=True)
    engine = C.CDLL(str(native.ROOT / TARGET))
    engine.four_play.argtypes = [C.POINTER(native.Config), C.c_uint64, C.c_int, C.c_int,
                                 C.POINTER(Result), C.c_size_t, C.c_size_t]
    engine.four_play.restype = C.c_int
    engine.multiplayer_play.argtypes = [
        C.POINTER(native.Config), C.c_int, C.c_uint64, C.c_int, C.c_int, C.POINTER(WEIGHT_PTR),
        C.c_int, C.POINTER(Result), C.c_size_t, C.c_size_t,
    ]
    engine.multiplayer_play.restype = C.c_int
    return engine


def board_seed(seed, index, seats=4):
    name = {3: "three", 4: "four"}[seats]
    key = f"monopoly/{name}/boards/{seed}/{index}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "little")


def count_trades(stats, result, seat, with_trading, seats=4):
    for sender in range(SEATS):
        classified = list(result.accepted_types[sender])
        if min(classified) < 0 or sum(classified) != sum(result.accepted[sender]):
            raise RuntimeError("Inconsistent counts of completed trades by type")
        if sender == seat:
            for key, count in zip(TRADE_TYPES, classified):
                stats[key] += count
        for recipient in range(SEATS):
            offered = result.offers[sender][recipient]
            closed = result.accepted[sender][recipient]
            if (not 0 <= closed <= offered or
                    (offered and (not with_trading or sender == recipient or max(sender, recipient) >= seats))):
                raise RuntimeError("Inconsistent negotiation accounting")
            if sender == seat:
                role = "outgoing"
            elif recipient == seat:
                role = "incoming"
            else:
                role = "between_opponents"
            stats[role + "_proposed"] += offered
            stats[role + "_accepted"] += closed


def prepare_weights(weights, seats):
    if weights is None:
        return None
    if len(weights) != seats:
        raise ValueError("Supply one multiplayer policy or None per seat")
    rows = []
    for row in weights:
        if row is None:
            rows.append(None)
            continue
        if len(row) != WEIGHT_COUNT or not all(math.isfinite(v) and -4 <= v <= 4 for v in row):
            raise ValueError("Each multiplayer policy needs 24 finite weights in [-4, 4]")
        rows.append((C.c_double * WEIGHT_COUNT)(*row))
    return rows


def play_board(engine, configs, opponents, index, seed, cap, with_trading, weights=None):
    seats = len(configs)
    if seats not in OPPONENT_ORDERS or len(opponents) != seats - 1:
        raise ValueError("Expected one entrant and two or three opponents")
    rows = prepare_weights(weights, seats)
    orders = OPPONENT_ORDERS[seats]
    order = (0, *orders[index % len(orders)])
    stats = Counter()
    for seat in range(seats):
        lineup = order[-seat:] + order[:-seat] if seat else order
        table = (native.Config * seats)(*(configs[i] for i in lineup))
        pointers = None if rows is None else (WEIGHT_PTR * seats)(*(rows[i] for i in lineup))
        result = Result()
        if not engine.multiplayer_play(table, seats, seed, cap, with_trading, pointers,
                                       WEIGHT_COUNT if pointers is not None else 0, C.byref(result),
                                       C.sizeof(native.Config), C.sizeof(result)):
            raise RuntimeError("Multiplayer engine rejected the tournament inputs")
        if not (-1 <= result.winner < seats and 1 <= result.survivors <= seats
                and 1 <= result.turns <= cap):
            raise RuntimeError("Invalid game result")
        if (result.survivors == 1) != (result.winner >= 0) or (result.winner < 0 and result.turns != cap):
            raise RuntimeError("Game ended before a winner or turn cap")
        if result.winner == seat:
            stats["wins"] += 1
            stats[f"seat_{seat}"] += 1
        elif result.winner >= 0:
            stats["losses"] += 1
            stats["winner_" + opponents[lineup[result.winner] - 1]] += 1
        else:
            stats["caps"] += 1
        stats["turns"] += result.turns
        count_trades(stats, result, seat, with_trading, seats)
    return stats


def checkpoint_info(name, model, weights=None):
    parts = ("board", "response", "opponent", "specialist", "resilience", "robust", "deal", "pressure")
    info = {
        "name": name,
        "style": model[0],
        "prior_weights_sha256": hashlib.sha256(json.dumps(model[1]).encode()).hexdigest(),
        "component_sha256": {part: obj.sha256 for part, obj in zip(parts, model[2:]) if obj is not None},
    }
    if weights is not None:
        info["multiplayer_weights_sha256"] = hashlib.sha256(json.dumps(list(weights)).encode()).hexdigest()
    return info


def benchmark(args):
    from multiplayer_model import load
    engine = load_engine()
    names = [args.model, *args.opponents]
    seats = len(names)
    # Keep every owner of a native network allocation alive until the last match finishes.
    models, weights = zip(*(load(name, seats) for name in names))
    configs = [native.config(model) for model in models]

    def play(index):
        return play_board(engine, configs, args.opponents, index, board_seed(args.seed, index, seats),
                          args.cap, not args.no_trades, weights)

    with ThreadPoolExecutor(args.workers) as pool:
        boards = list(pool.map(play, range(args.boards)))

    total = Counter()
    for board in boards:
        total.update(board)
    games = seats * args.boards
    fraction = total["wins"] / games
    interval = None
    if args.boards > 1:
        rates = [row["wins"] / seats for row in boards]
        se = statistics.stdev(rates) / math.sqrt(args.boards)
        interval = [max(0., fraction - 1.96 * se), min(1., fraction + 1.96 * se)]
    receipt = {
        "model": args.model, "opponents": args.opponents, "seats": seats,
        "policies": [checkpoint_info(*parts) for parts in zip(names, models, weights)],
        "engine_sha256": hashlib.sha256((native.ROOT / TARGET).read_bytes()).hexdigest(),
        "board_seed": args.seed, "independent_boards": args.boards, "games": games,
        "turn_cap": args.cap, "trading": not args.no_trades,
        "wins": total["wins"], "losses": total["losses"], "caps": total["caps"],
        "win_rate": fraction, "board_cluster_95_interval": interval,
        "wins_by_starting_seat": [total[f"seat_{i}"] for i in range(seats)],
        "opponent_wins_by_model": {n: total["winner_" + n] for n in sorted(set(args.opponents))},
        "mean_individual_turns": total["turns"] / games,
        "trades": {
            role: {key: total[role + "_" + key] for key in ("proposed", "accepted")}
            for role in ("outgoing", "incoming", "between_opponents")
        },
        "accepted_outgoing_trade_types": {key: total[key] for key in TRADE_TYPES},
        "design": ("Each game has one shared board. Each start rotates the entrant through "
                   "every seat. Opponent order cycles through all permutations. Only the last solvent "
                   "player wins. Games unresolved at the turn cap count as non-wins. The approximate 95% "
                   "interval uses the variation in win fractions across independent starts."),
        "adapter": ("Multiplayer checkpoints use additional full-table features. Base duel decisions "
                    "evaluate one rival at a time. Each active player can consider every surviving rival "
                    "for one offer of up to one deed each way plus cash. The simulator enforces the full "
                    "table's ownership and building supply."),
    }
    if args.report:
        save(args.report, receipt)
    print(json.dumps(receipt, indent=2))
