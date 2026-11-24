"""Command-line interface for training, measuring and entering competitions."""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tempfile

from native import (CALLBACK, INT, MODEL, LEGACY_MODEL, PROTOCOL, ROOT, Reply, Snapshot, Request,
                    board_info, choose, config, evaluate_trade, integer, lib, make_packet, model)
from training import (DEFAULT_CAP, EVAL_SEED, TRAIN_SEED, WORKERS, derived, evaluate, save,
                      train, wilson)
import four_player


def example(scenario="buy"):
    v = Snapshot()
    v.cash[:] = [1500, 1500]
    v.position[:] = [6, 0]
    v.jail[:] = [-1, -1]
    v.owner[:] = [-1] * 40
    v.houses, v.hotels, v.turn, v.active = 32, 12, 1, 0
    if scenario == "buy":
        return make_packet(v, 0, Request(0, 6, 100, -1, -1, 0), "demo", 1)
    v.cash[:] = [800, 820]
    v.position[:] = [18, 24]
    v.active, v.turn = 0, 29
    for sq in (16, 18, 27, 35):
        v.owner[sq] = 0
    for sq in (19, 26, 29, 15):
        v.owner[sq] = 1
    if scenario in ("bundle-offer","bundle-trade"):
        v.cards[0]=1
        giving = scenario=="bundle-offer"
        request = make_packet(v,0 if giving else 1,
                              Request(5 if giving else 6,-1,0,-1,-1,0),"demo",1)
        request["capabilities"]=["multi_deed_trades_v1"]
        if not giving:
            request["query"].update(give_deeds=[16,18],take_deeds=[26],give_cards=["chance"],cash=60)
        return request
    if scenario == "offer":
        return make_packet(v, 0, Request(5, -1, 0, -1, -1, 0), "demo", 1)
    return make_packet(v, 1, Request(6, -1, 0, 27, 19, 75), "demo", 1)


def serve(args):
    from series_player import competition_player
    player = competition_player(args.model,args.adapt_series)
    for line in sys.stdin:
        reply = player.choose(json.loads(line))
        print(json.dumps(reply, separators=(",", ":")), flush=True)


class LocalBot:
    def __init__(self, command, timeout):
        self.timeout = timeout
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        bufsize=0)
        self.responses = queue.Queue(maxsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            line = self.process.stdout.readline(65537)
            self.responses.put(line)
            if not line or len(line) > 65536:
                return

    def ask(self, packet):
        self.process.stdin.write(json.dumps(packet, separators=(",", ":")).encode() + b"\n")
        try:
            line = self.responses.get(timeout=self.timeout)
        except queue.Empty as exc:
            raise ValueError("Bot timed out waiting for a reply") from exc
        if not line or len(line) > 65536 or not line.endswith(b"\n"):
            raise ValueError("Bot exited or returned an incomplete/oversized reply")
        return json.loads(line)

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()


def local_game(strategy, choose_reply, seed, cap, seat, game_id):
    errors = []
    requests = 0

    @CALLBACK
    def handle(s, p, q, out):
        nonlocal requests
        try:
            requests += 1
            packet = make_packet(s.contents, p, q.contents, game_id, requests)
            answer = choose_reply(packet)
            if (answer["protocol"] != PROTOCOL or answer["game_id"] != game_id
                    or answer["request_id"] != requests):
                raise ValueError("Reply protocol or request identifiers do not match")
            a = answer["action"]
            out[0] = Reply(integer(a.get("value", 0), 0, 100000000),
                           *(integer(a.get(k, -1), -1, 39) for k in ("square", "give", "take")),
                           integer(a.get("cash", 0), -100000000, 100000000))
            return 1
        except BaseException as exc:
            errors.append(exc)
            return 0

    turns = INT()
    winner = lib.mono_external(config(strategy), seed, cap, seat,
                               handle, C.byref(turns))
    if errors:
        raise errors[0]
    return {"game_id": game_id, "our_seat": 1-seat, "winner": winner, "turns": turns.value,
            "rival_decisions": requests}


def duel(args):
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("Supply the other bot command after --")
    strategy = model(args.model)
    bot = LocalBot(command, args.timeout)
    games = []
    try:
        for i in range(args.games):
            data = local_game(strategy, bot.ask, derived("duel", args.seed, i), args.cap,
                              1-i%2, f"game-{i+1}")
            games.append(data)
            print(json.dumps(data), flush=True)
    finally:
        bot.close()
    wins = sum(g["winner"] == g["our_seat"] for g in games)
    caps = sum(g["winner"] == -1 for g in games)
    summary = dict(games=len(games), wins=wins, losses=len(games)-wins-caps, caps=caps,
                   win_rate=wins/len(games), win_rate_95=wilson(wins, len(games)))
    print(json.dumps(summary))
    if args.report:
        save(args.report, dict(seed=args.seed, turn_cap=args.cap, matches=games, summary=summary))


def check():
    from native import batch, preset
    demo = example()
    assert choose(preset(0), demo)["action"]["value"] in (0, 1)
    # Running the same rival via the public interface must reproduce the native matchup.
    # mono_batch maps each starting seed through a single SplitMix64 draw.
    def mix(seed):
        mask = (1 << 64)-1
        z = (seed + 0x9e3779b97f4a7c15) & mask
        z = ((z ^ (z >> 30))*0xbf58476d1ce4e5b9) & mask
        z = ((z ^ (z >> 27))*0x94d049bb133111eb) & mask
        return z ^ (z >> 31)
    strategies = [(preset(i%4), preset((i+1)%4)) for i in range(16)]
    competition = model() if MODEL.exists() else preset(5)
    strategies.extend([(competition, preset(4)), (preset(4), competition),
                       (preset(0), competition), (preset(1), competition)])
    for seed, (a, b) in enumerate(strategies):
        native = batch(a, b, seed, 1, DEFAULT_CAP)
        g = local_game(a, lambda packet: choose(b, packet), mix(seed), DEFAULT_CAP, 1, "selftest")
        outcome = {0:"wins", 1:"losses", -1:"caps"}[g["winner"]]
        assert g["turns"] == native["turns"] and native[outcome] == 1
    print(f"Public decision protocol reproduced all {len(strategies)} native games.")
    if competition[0] >= 6:
        outgoing = choose(competition, example("offer"))["action"]
        assert outgoing["give"] >= 0 or outgoing["take"] >= 0
        incoming = example("trade")
        for cash, expected in ((650, "accept"), (-650, "decline")):
            incoming["query"]["cash"] = cash
            assessment = evaluate_trade(competition, incoming)
            assert assessment["decision"] == expected
            assert bool(choose(competition, incoming)["action"]["value"]) == (expected == "accept")
            assert 0 <= assessment["estimated_acceptance"] <= 1
        print("The shipped networks proposed a trade and evaluated both acceptance and rejection.")


def positive(value):
    n = int(value)
    if not 0 < n <= 100000000:
        raise argparse.ArgumentTypeError("Expected a positive integer up to 100 million")
    return n


def seconds(value):
    v = float(value)
    if not 0 < v <= 3600:
        raise argparse.ArgumentTypeError("Expected seconds in (0, 3600]")
    return v


def train_networks(args):
    # The player is dependency-free at runtime. Training runs in a Python
    # installation with NumPy so it can use its optimized matrix operations.
    interpreter = os.environ.get("MONOPOLY_TRAIN_PYTHON", "python3")
    mode=args.mode
    if mode=="train":
        current=json.loads((ROOT/"models/adaptive.json").read_bytes())["format"]
        mode={"monopoly.adaptive.v1":"train-v1","monopoly.adaptive.v2":"train-candidate",
              "monopoly.adaptive.v3":"train-league","monopoly.adaptive.v4":"train-pressure"}[current]
    default_name = {"train-candidate":"trained-candidate", "train-league":"trained-league",
                    "train-pressure":"trained-pressure"}.get(args.mode,"trained-player")
    target = (args.directory or ROOT/"build"/default_name).resolve()
    target.mkdir(parents=True, exist_ok=True)
    reports = (ROOT/"reports" if target == MODEL.parent else target)
    reports.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OPENBLAS_NUM_THREADS="2", VECLIB_MAXIMUM_THREADS="2", OMP_NUM_THREADS="2")
    with tempfile.TemporaryDirectory(prefix=".training-", dir=target) as tmp:
        stage = Path(tmp)
        # Stage the complete checkpoint before exposing it to competition clients.
        subprocess.run([interpreter, str(ROOT/"response_train.py"), "--games", str(args.response_games),
                        "--output", str(stage/"response.bin"), "--report", str(stage/"response-training.json")],
                       cwd=ROOT, env=env, check=True)
        subprocess.run([interpreter, str(ROOT/"neural_train.py"), "--games", str(args.games),
                        "--epochs", str(args.epochs), "--workers", str(args.workers), "--seed", str(args.seed),
                        "--calibration-games", str(args.calibration_games), "--validation-games", str(args.validation_games),
                        "--out", str(stage/"neural.bin"), "--report", str(stage/"neural-training.json")],
                       cwd=ROOT, env=env, check=True)
        model(stage/"neural.json")  # Verify both checksums and native loaders before publishing.
        logs = ["response-training.json", "neural-training.json"]
        if mode != "train-neural":
            subprocess.run([interpreter, str(ROOT/"opponent_train.py"), "--output", str(stage/"opponent.bin"),
                            "--report", str(stage/"opponent-training.json")], cwd=ROOT, env=env, check=True)
            subprocess.run([interpreter, str(ROOT/"specialist_train.py"), "--model-directory", str(stage),
                            "--opponent-net", str(stage/"opponent.bin"), "--output", str(stage/"specialist.json"),
                            "--workers", str(args.workers), "--report", str(stage/"specialist-training.json")],
                           cwd=ROOT, env=env, check=True)
            from adaptive_manifest import write_manifest
            write_manifest(stage)
            logs += ["opponent-training.json","specialist-training.json"]
        if mode in ("train-candidate","train-league","train-pressure"):
            subprocess.run([interpreter, str(ROOT/"resilience_train.py"), "--model-directory", str(stage),
                            "--output", str(stage/"resilience.json"), "--report", str(stage/"resilience-training.json"),
                            "--workers", str(args.workers)], cwd=ROOT, env=env, check=True)
            write_manifest(stage, bidding_model=True)
            logs += ["resilience-training.json"]
        if mode in ("train-league","train-pressure"):
            for script, weight, report in (("robust_train.py","robust.json","robust-training.json"),
                                          ("deal_train.py","deal.bin","deal-training.json")):
                subprocess.run([interpreter,str(ROOT/script),"--model-directory",str(stage),
                                "--output",str(stage/weight),"--report",str(stage/report),
                                "--workers",str(args.workers)],cwd=ROOT,env=env,check=True)
                logs.append(report)
            write_manifest(stage,league=True,deal_model=True)
        if mode == "train-pressure":
            subprocess.run([interpreter,str(ROOT/"pressure_train.py"),"--model-directory",str(stage),
                            "--output",str(stage/"pressure.json"),"--report",str(stage/"pressure-training.json"),
                            "--workers",str(args.workers)],cwd=ROOT,env=env,check=True)
            write_manifest(stage,pressure_mode="observed")
            logs.append("pressure-training.json")
        from checkpoint_publish import publish_checkpoint
        entry = publish_checkpoint(stage, target, reports, mode != "train-neural", logs)
    print(f"Trained player saved in {entry}", flush=True)


def main():
    if sys.argv[1:2] == ["train-multiplayer"]:
        os.execv(sys.executable, [sys.executable, str(ROOT / "multiplayer_train.py"), *sys.argv[2:]])
    parser = argparse.ArgumentParser(description="Classic US Monopoly player and simulator")
    subs = parser.add_subparsers(dest="mode", required=True)
    from table_server import configure as configure_table
    configure_table(subs.add_parser("table", help="Host a browser game for people and AI players"))
    for command, seats in (("three-player", 3), ("four-player", 4)):
        tournament = subs.add_parser(command, help=f"Run a {seats}-player local tournament")
        four_player.configure(tournament, positive, seats)
    subs.add_parser("train-multiplayer", help="Train a neural policy for a three- or four-player table")
    n = subs.add_parser("train", aliases=["train-neural","train-v1","train-candidate","train-league","train-pressure"], help="Train the deployed neural player or a named generation")
    n.add_argument("--games", type=positive, default=1000000)
    n.add_argument("--response-games", type=positive, default=50000)
    n.add_argument("--epochs", type=positive, default=6)
    n.add_argument("--workers", type=positive, default=min(WORKERS, 8))
    n.add_argument("--seed", type=int, default=714335017)
    n.add_argument("--calibration-games", type=positive, default=20000)
    n.add_argument("--validation-games", type=positive, default=40000)
    n.add_argument("--directory", type=Path, help="Output folder, default build/trained-player or build/trained-candidate")
    t = subs.add_parser("train-legacy", help="Train the original strategy parameters")
    t.add_argument("--games", type=positive, default=1_000_000)
    t.add_argument("--generations", type=positive, default=20)
    t.add_argument("--output", type=Path, default=LEGACY_MODEL)
    t.add_argument("--report", type=Path, default=ROOT/"reports"/"training.json")
    e = subs.add_parser("evaluate", help="Run fresh seat-balanced games per opponent")
    e.add_argument("--games", type=positive, default=2000, help="Games per opponent")
    e.add_argument("--opponents", nargs="+", default=["legacy","blocker","collector","builder","cautious","mirror"])
    e.add_argument("--report", type=Path, default=ROOT/"reports"/"evaluation-latest.json")
    for p, seed in ((t, TRAIN_SEED), (e, EVAL_SEED)):
        p.add_argument("--workers", type=positive, default=WORKERS)
        p.add_argument("--seed", type=int, default=seed)
    bundles = subs.add_parser("bundle-match", help="Play complete local games with negotiated packages and jail cards")
    bundles.add_argument("--opponent",default="collector")
    bundles.add_argument("--games",type=positive,default=2)
    bundles.add_argument("--seed",type=int,default=85501837)
    bundles.add_argument("--cap",type=positive,default=1000)
    bundles.add_argument("--report",type=Path)
    bot = subs.add_parser("bot", help="Choose actions over JSON Lines on stdin/stdout")
    a = subs.add_parser("assess", help="Explain an incoming trade from a JSON Lines decision packet")
    sc = subs.add_parser("scout", help="Show learned opponent estimates for a public decision packet")
    d = subs.add_parser("duel", help="Play against another trusted local bot process")
    d.add_argument("--games", type=positive, default=2)
    d.add_argument("--seed", type=int, default=67521)
    d.add_argument("--timeout", type=seconds, default=10)
    d.add_argument("--report", type=Path)
    d.add_argument("command", nargs=argparse.REMAINDER)
    for p in (t, e, d):
        p.add_argument("--cap", type=positive, default=DEFAULT_CAP, help="Maximum individual player turns per game")
    c = subs.add_parser("connect", help="Join a compatible host over HTTP polling")
    c.add_argument("--poll-url", required=True)
    c.add_argument("--submit-url", required=True)
    c.add_argument("--token-env", help="Environment variable holding the optional bearer token")
    c.add_argument("--timeout", type=seconds, default=15)
    c.add_argument("--interval", type=seconds, default=1)
    for p in (bot, d, e, c, a, sc, bundles):
        p.add_argument("--model", default="champion", help="Model path, champion or baseline name")
    for p in (bot,c):
        p.add_argument("--adapt-series", action="store_true", help="Learn which neural checkpoint wins linked rematches from host-reported outcomes")
    subs.add_parser("board", help="Print the board and all title deed values as JSON")
    ex = subs.add_parser("example", help="Print one complete decision request")
    ex.add_argument("scenario", nargs="?", default="buy", choices=["buy","offer","trade","bundle-offer","bundle-trade"])
    subs.add_parser("check", help="Verify protocol parity against the native simulator")
    args = parser.parse_args()
    if args.mode == "table":
        from table_server import host
        return host(args)
    if args.mode in ("three-player", "four-player"):
        return four_player.benchmark(args)
    if args.mode == "bundle-match":
        from native import bundle_batch
        stats = bundle_batch(model(args.model),model(args.opponent),args.seed,args.games,args.cap)
        report = dict(model=args.model,opponent=args.opponent,seed=args.seed,cap=args.cap,
                      games=args.games,**stats)
        if args.report:
            save(args.report,report)
        print(json.dumps(report,indent=2))
        return
    if args.mode == "connect":
        from remote_client import connect
        return connect(args)
    if args.mode in ("assess","scout"):
        from native import scout
        inspect = scout if args.mode == "scout" else evaluate_trade
        strategy = model(args.model)
        for line in sys.stdin:
            print(json.dumps(inspect(strategy, json.loads(line))), flush=True)
    elif args.mode == "board":
        print(json.dumps(board_info(), indent=2))
    elif args.mode == "example":
        print(json.dumps(example(args.scenario)))
    elif args.mode == "check":
        check()
    else:
        {"bot":serve,"duel":duel,"train":train_networks,"train-neural":train_networks,"train-v1":train_networks,"train-candidate":train_networks,"train-league":train_networks,"train-pressure":train_networks,
         "train-legacy":train,"evaluate":evaluate}[args.mode](args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Monopoly: {error}")
    except KeyboardInterrupt:
        sys.exit(130)
