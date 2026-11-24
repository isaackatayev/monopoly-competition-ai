"""Fit a three-player specialist against two Collectors and qualify it once on fresh boards."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

import four_player
import multiplayer_model
import multiplayer_train as fit
import native
from training import derived, save

FORMAT = "monopoly.collector-specialist.v1"
SEATS = 3
OPPONENTS = ("collector", "collector")
ZERO = fit.ZERO


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def head(vector):
    if not any(vector[8:16]):
        return "acquisition"
    if not any(vector[:8]):
        return "negotiation"
    return "both"


def warm_start(path, general):
    archive = json.loads(path.read_bytes())
    if archive["settings"]["seats"] != SEATS or archive["fixtures"][0] != list(OPPONENTS):
        raise ValueError("Expected a three-seat training archive with a two-Collector fixture")
    # Rank prior policies by performance on their own matching control boards;
    # modest shrinkage keeps a single lucky short stage from dominating.
    previous = defaultdict(lambda: [0, 0])
    for stage in [*archive["rounds_completed"], archive["validation"]]:
        baseline = stage["candidates"][0]
        if any(baseline["weights"]):
            raise ValueError("Training archive has no frozen v4 control")
        reference = baseline["fixture_wins_losses_caps"][0]
        for row in stage["candidates"][1:]:
            result = row["fixture_wins_losses_caps"][0]
            if sum(result) != sum(reference):
                raise ValueError("Training archive contains unpaired board counts")
            agg = previous[tuple(row["weights"])]
            agg[0] += result[0] - reference[0]
            agg[1] += sum(result)
    ranks = sorted(previous, key=lambda v: previous[v][0] / (previous[v][1] + 100), reverse=True)
    candidates = [ZERO, general, fit.isolated(general, "acquisition"), fit.isolated(general, "negotiation")]
    for mode, count in (("acquisition", 9), ("negotiation", 5), ("both", 5)):
        candidates.extend([v for v in ranks if head(v) == mode][:count])
    candidates.extend(fit.profile(b, 0) for b in (-.30, -.10, -.025, .025, .10, .20, .28, .32, .48, .75))
    candidates.extend(fit.profile(0, t) for t in (-.55, -.10, -.015, .015, .10, .50))
    for factor in (.15, .45, .80):
        candidates.append(fit.isolated(tuple(fit.clipped(w * factor) for w in general), "acquisition"))
    return list(dict.fromkeys(candidates))


def interval(differences):
    mean = statistics.mean(differences)
    radius = 1.96 * statistics.stdev(differences) / math.sqrt(len(differences))
    return {"delta": mean, "paired_95_interval": [max(-1., mean-radius), min(1., mean+radius)]}


class Arena:
    def __init__(self, workers, cap, seed):
        self.engine = four_player.load_engine()
        self.models = [native.model(name) for name in (fit.BASE, *OPPONENTS)]
        self.configs = [native.config(model) for model in self.models]
        self.pool = ThreadPoolExecutor(workers)
        self.cap = cap
        self.seed = seed

    def evaluate(self, vectors, domain, boards):
        seeds = [derived(FORMAT, self.seed, domain, i) for i in range(boards)]

        def play(job):
            vector, index = job
            r = four_player.play_board(self.engine, self.configs, OPPONENTS, index, seeds[index],
                                       self.cap, True, [None if vector == ZERO else vector, None, None])
            return r["wins"], r["losses"], r["caps"]

        jobs = ((v, i) for v in vectors for i in range(boards))
        raw = iter(self.pool.map(play, jobs))
        scores, samples = [], []
        for vector in vectors:
            board_results = [next(raw) for _ in seeds]
            wins, losses, caps = map(sum, zip(*board_results))
            assert wins + losses + caps == SEATS * boards
            samples.append([row[0] / SEATS for row in board_results])
            scores.append({"weights": list(vector), "wins": wins, "losses": losses,
                           "caps": caps, "win_rate": wins / (SEATS * boards)})
        return scores, samples


def next_generation(scores, size, scale, rng):
    ranking = sorted((row for row in scores if any(row["weights"])), key=lambda r: r["wins"], reverse=True)
    top = [tuple(r["weights"]) for r in ranking[:5]]
    for mode in ("acquisition", "negotiation"):
        match = next((tuple(r["weights"]) for r in ranking if head(r["weights"]) == mode), None)
        if match and match not in top:
            top.append(match)
    slate = [ZERO, *top]
    # Preserve the trading behavior of acquisition-only parents and vice versa.
    # Occasionally mix two parents or perturb the other head to explore joint fits.
    while len(slate) < size:
        p = rng.choice(top)
        if len(slate) % 4 == 1:
            q = rng.choice(top)
            p = tuple((a+b)*.5 for a,b in zip(p,q))
        mode = head(p) if rng.random() < .75 else rng.choice(("acquisition", "negotiation", "both"))
        w = fit.isolated(fit.mutate(p, scale * rng.uniform(.30, 1.3), rng), mode)
        if w not in slate:
            slate.append(w)
    return slate[:size]


def run(args):
    _, general = multiplayer_model.load(str(args.general), SEATS)
    if general is None:
        raise ValueError("A trained multiplayer generalist is required")
    general = tuple(general)
    arena = Arena(args.workers, args.cap, args.seed)
    try:
        vectors = warm_start(args.archive, general)
        stages = []
        for i, n in enumerate((36, 72, 105)):
            results, _ = arena.evaluate(vectors, f"fit-{i}", n)
            stages.append({"boards": n, "candidates": results})
            print(f"Collector search {i+1}: {len(results)} candidates, {len(results)*n*3:,} games, "
                  f"best {max(r['win_rate'] for r in results):.1%}, v4 {results[0]['win_rate']:.1%}", flush=True)
            if i < 2:
                vectors = next_generation(results, (23, 16)[i], (.42, .22)[i],
                                          random.Random(derived(FORMAT, args.seed, "mutation", i)))
        slate = [ZERO, general]
        # Give preference to the best policies on the largest, latest cohort.
        for stage, slots in zip(reversed(stages), (4, 2, 1)):
            added = 0
            for row in sorted(stage["candidates"], key=lambda r: r["wins"], reverse=True):
                w = tuple(row["weights"])
                if w in slate:
                    continue
                slate.append(w)
                added += 1
                if added == slots:
                    break
        selection, _ = arena.evaluate(slate, "select-once", 300)
        chosen = tuple(max(selection[2:], key=lambda r: r["wins"])["weights"])
        locked_path = fit.ROOT / "build" / "multiplayer-training" / "candidate-3-collectors.json"
        checkpoint = multiplayer_model.candidate(SEATS, chosen)
        save(locked_path, checkpoint)
        outcomes, samples = arena.evaluate([ZERO, general, chosen], "holdout-once", args.test_boards)
    finally:
        arena.pool.shutdown()
    names = ("original_v4", "multiplayer_generalist", "collector_specialist")
    paired = {name: interval([x-y for x,y in zip(samples[2], samples[index])])
              for index,name in enumerate(names[:2])}
    qualifies = paired["original_v4"]["paired_95_interval"][0] > 0
    if qualifies:
        save(args.specialist, checkpoint)
    receipt = {
        "format": FORMAT, "seats": SEATS, "opponents": OPPONENTS, "seed": args.seed,
        "turn_cap": args.cap, "engine_sha256": digest(fit.ROOT / four_player.TARGET),
        "source_archive_sha256": digest(args.archive), "generalist_sha256": digest(args.general),
        "locked_specialist_sha256": digest(locked_path), "stages": stages,
        "selection": {"boards": 300, "candidates": selection},
        "fresh_search_and_selection_games": sum(s["boards"]*len(s["candidates"])*SEATS for s in stages)
                                             + len(selection)*300*SEATS,
        "holdout": {"independent_starts": args.test_boards, "games_per_policy": SEATS * args.test_boards,
                    "outcomes": dict(zip(names,outcomes)), "specialist_minus_reference": paired},
        "specialist_qualified": qualifies,
        "published_specialist": str(args.specialist.name) if qualifies else None,
        "method": ("Policies from the multiplayer training archive and single-head acquisition and trade "
                   "policies seed three rounds of Collector-only evolutionary search. Separate fixed "
                   "seed domains supply search, selection and final verification. Each start is played "
                   "from all three seats and all policies use the same boards for a comparison. One "
                   "specialist locks before the final matchups. Only a paired 95% interval strictly above "
                   "zero against original v4 qualifies it for optional use at two-Collector tables."),
    }
    save(args.report, receipt)
    print(json.dumps({"fresh_search_and_selection_games": receipt["fresh_search_and_selection_games"],
                      "holdout": {k:round(r["win_rate"],4) for k,r in zip(names,outcomes)},
                      "paired": paired, "qualified": qualifies}, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--general", type=Path, default=fit.ROOT/"build/multiplayer-training/candidate-3.json")
    p.add_argument("--archive", type=Path, default=fit.ROOT/"reports/multiplayer-training-3.json")
    p.add_argument("--report", type=Path, default=fit.ROOT/"reports/multiplayer-3-collectors.json")
    p.add_argument("--specialist", type=Path, default=fit.ROOT/"models/multiplayer-3-collectors.json")
    p.add_argument("--seed", type=int, default=669854103)
    p.add_argument("--workers", type=fit.positive, default=6)
    p.add_argument("--cap", type=fit.positive, default=1000)
    p.add_argument("--test-boards", type=fit.positive, default=1500)
    opts = p.parse_args()
    if opts.test_boards < 2 or opts.workers > 6:
        p.error("Use at least two final boards and at most six workers")
    if len({x.resolve() for x in (opts.archive,opts.general,opts.report,opts.specialist)}) != 4:
        p.error("Use distinct paths for each input and output")
    run(opts)
