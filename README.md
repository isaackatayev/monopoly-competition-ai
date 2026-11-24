# Monopoly Competition AI

**Made by Isaac Katayev.**

A Monopoly player that learns from simulated games and negotiates using the same public information as its opponents. Four trained networks help it value a board, price a deal, predict whether an offer will be accepted, and interpret a rival’s behavior. The repository includes the C++17 simulator, a playable browser table, training code, model weights, and match-by-match evaluation tools.

## Run the competition player

On macOS or Linux, install Make, a C++17 compiler, and Python 3.9 or newer. With GitHub access to this private repository, run the following commands. The first example asks the trained bot to propose a trade and prints its decision as JSON.

```sh
git clone https://github.com/isaackatayev/monopoly-competition-ai.git
cd monopoly-competition-ai
make
./monopoly example offer | ./monopoly bot
```

To play in a browser, run `./monopoly table`, open the printed local address, and choose **Start a game**. To watch the competition player face the previous champion in the terminal, run `./monopoly duel --games 4 -- ./monopoly bot --model legacy`.

## Choose a trained player

| Format | Checkpoint | Recorded result |
| --- | --- | --- |
| Heads-up competition | [Adaptive v4](models/adaptive-v4.json), the default for `./monopoly bot` | 26,053 wins in 36,000 games against 18 opponents (72.37%). [League results](reports/league-v4.json) |
| Browser table | [Club Champion](models/table-champion.json), used by [the table agent](champion_table.py) | 691 wins in 1,024 held-out games. It spent $33,603 over face value at auction after declining a purchase, down from $69,056 for the original table policy on the same games. [Table evaluation](reports/table-champion-training.json) |
| Three players | [Three-player network](models/multiplayer-3.json), the default for `./monopoly three-player` | 5,472 wins in 10,800 games (50.67%). [Qualification](reports/multiplayer-qualification-3.json) |
| Four players | [Four-player network](models/multiplayer-4.json), the default for `./monopoly four-player` | 6,802 wins in 14,400 games (47.24%). [Qualification](reports/multiplayer-qualification-4.json) |

Each row uses its own opponents and rules. The browser checkpoint was selected for purchase pricing and cash availability at auctions. Keep the entire `models/` directory together when copying a player. Its manifests reference shared weights and record their SHA-256 checksums.

## Explore the project

- **Game engine and players.** Follow the [native simulator and decision code](src/) or inspect the [trained model files](models/).
- **Training and evaluation.** Read the [research notes](RESEARCH.md), [reproduction commands](#reproduce-and-verify), and [archived reports](reports/).
- **Connect an agent.** Use the [competition decision contract](COMPETITION.md) for the heads-up bot or the [table protocol](TABLE_PROTOCOL.md) for a human or AI seat.
- **Browser and tests.** Browse the [table interface](web_table/) and [rules, model, and protocol tests](tests/).

## Host a browser table

The table launcher requires Python 3.9+, Make and a C++17 compiler. It builds the simulator as needed.

**Play over the internet with ngrok.** With ngrok installed and signed in, start a tunnel in one terminal.

```sh
ngrok http 127.0.0.1:8766 --inspect=false
```

In a second terminal, start the game with the HTTPS forwarding URL printed by ngrok in place of the example below. Keep both processes running during play. [ngrok CLI quickstart](https://ngrok.com/docs/gateway/endpoints/agent-cli-quickstart)

```sh
./monopoly table --port 8766 --public-url https://YOUR-NGROK-DOMAIN
```

**Play locally.** Run `./monopoly table` for this computer or `./monopoly table --lan` to also accept players on the same network. Open the printed local address. For LAN play, both machines must be able to reach the host on the selected port (default 8765).

Open the game in a browser and choose **Start a game**. Take a seat yourself or host two guests as a spectator. Under **Invite players**, use **Copy link** for a person or **Copy AI invitation** for an agent with HTTP access. With ngrok configured, both invites use the public HTTPS address, even when you create the table on localhost. Guests can join from another network. The game starts when both seats fill.

The referee controls rolls, money, auctions, trades and the result. The host serves the browser interface, public table protocol and a standalone example client. The competition players and their weights stay private. Games and seat credentials live in the server process. Restarting it clears the tables. [AI connection specification](TABLE_PROTOCOL.md) · [Example agent](example_table_agent.py)

## Play as Club Champion

**Club Champion is Adaptive v4 fine-tuned for the browser table.** It reuses all four v4 neural networks and changes eight policy parameters governing purchase prices, cash reserves, and funding for houses. Its table policy can also bid to block a rival’s last street and look for trades on successive turns. Use the model selector `table-champion` to load both the tuned weights and those table-specific decisions. [Table manifest](models/table-champion.json) · [Tuned weights](models/table-training/) · [Bidding policy](src/pressure_policy.cpp) · [Trade policy](src/neural_trade.cpp)

To give Club Champion a seat, copy the browser invite for a table with an open seat. After running `make`, substitute that invite below. Use a fresh pair of files for each table and reuse the session file to reconnect to the same seat.

```sh
python3 champion_table.py 'PASTE_FULL_TABLE_INVITE_HERE' --session build/champion-seat.json --log build/champion-decisions.jsonl
```

Run `python3 table_champion_train.py` to repeat the search over eight purchase and financing weights. The trainer scores simulated games on results, dollars overpaid after declining a direct purchase, and cash available at auctions for an opponent’s last missing street. It tests the selected weights on fresh seeds with seats swapped. [Training results](reports/table-champion-training.json)

## Competition checkpoint

`./monopoly bot` and `./monopoly connect` use **adaptive v4**. On 36,000 games against 18 equally weighted opponent fixtures, it won **26,053 (72.37%)**. The previous default, v1, won 25,170 (69.92%) on the same boards and seats. The gain was 2.45 percentage points, with a paired 95% interval of 1.99 to 2.92 points. Each game had a 1,000-turn cap. A capped game counted as a non-win. [Full qualification](reports/league-v4.json)

| Opponent | Previous default | V4 |
| --- | ---: | ---: |
| Previous default itself | 49.45% | 54.65% |
| Rollout champion | 60.95% | 61.20% |
| Trade-refusing blocker | 76.80% | 76.80% |
| First archived challenger | 47.05% | 56.30% |
| Second archived challenger | 46.05% | 60.90% |
| Third archived challenger | 42.80% | 49.40% |
| New challenger A | 51.25% | 50.20% |
| New challenger B | 35.70% | 35.70% |
| New challenger C | 22.65% | 27.75% |

Every row uses 2,000 games per entrant. A, B and C each had 4,416 games to train against the locked v4 checkpoint. Only A appears in the 18-opponent headline. These results give three independent attempts to find a weakness. The organizer's entrant has yet to play. [Main league](reports/league-v4.json) · [Challenger B](reports/league-v4-b.json) · [Challenger C](reports/league-v4-c.json)

The new bidding policy estimates how aggressively the opponent prices deeds from public purchases, auction results and cash. It can pay more to keep a rival from completing a color group. On identical boards against the third archived challenger, v4's win rate increased from v3's 20.80% to 49.40%. It gave up 7.60 points against the first archived challenger and 4.25 against the additive bidder. [Paired policy comparison](reports/league-v4-pricing.json) · [Bidding training](reports/pressure-training.json)

With package trading enabled, v4 won **229/400 (57.25%)** against the previous default, 384/400 against the collector and 305/400 against the blocker. In the first matchup both players negotiated complex deals and completed 240 packages, including 26 involving jail cards. Package proposals use the board network, a recipient-willingness heuristic and paired simulated futures. A fourth network trained on single-deed trades helps evaluate incoming and outgoing scalar offers when the rival shows selective trading. [Package games](reports/bundle-v4.json) · [Package decision experiment](reports/package-verification.json) · [Single-deed network experiment](reports/deal-ablation-v3.json)

## Multiplayer training

On fresh boards, the trained three-player default won **5,472 of 10,800 games (50.67%)**, up from 4,880 (45.19%) for the original v4. The four-player default won **6,802 of 14,400 games (47.24%)**, up from 4,547 (31.58%). The paired 95% intervals for the improvements were **4.29–6.68** and **14.61–16.71** percentage points, respectively. Each table below received equal weight and used 600 fresh starts, replayed from every seat. [Three-player qualification](reports/multiplayer-qualification-3.json) · [Four-player qualification](reports/multiplayer-qualification-4.json)

| Opponents at the table | 3-player v4 | 3-player trained | 4-player v4 | 4-player trained |
| --- | ---: | ---: | ---: | ---: |
| All Collectors | 73.1% | 68.6% | 37.2% | 43.8% |
| Mixed basic bots | 60.4% | 60.9% | 43.1% | 49.4% |
| Earlier neural checkpoints | 32.3% | 37.3% | 34.8% | 55.0% |
| Blocker and basic bots | 41.7% | 54.1% | 18.6% | 38.1% |
| V4 in every opposing seat | 28.6% | 45.0% | 19.3% | 57.2% |
| Blocker and neural bots | 35.1% | 38.2% | 36.4% | 39.9% |

**For a known three-player table with two Collectors, use `--model adaptive-v4`.** A further 22,644 games trained and selected a specialist for that matchup. On 4,500 fresh games per policy, the original v4 won 72.98%, the specialist won 72.47%, and the multiplayer generalist won 69.56%. The specialist’s paired 95% interval versus v4 ran from −1.88 to +0.85 percentage points. [Collector specialist test](reports/multiplayer-3-collectors.json)

For each format, a new rival then trained at tables containing the locked checkpoint. On independently seeded games against that rival and rotating background opponents, the three-player model won **545/1,206 (45.19%)**, compared with 404/1,206 (33.50%) for v4. The four-player model won **482/1,200 (40.17%)**, compared with 223/1,200 (18.58%) for v4. Replaying the 600 starts from the earlier three-Collector comparison in all four seats, the four-player model improved from 860 wins to 1,045 (35.83% to 43.54%). Its accepted offers included 617 cash purchases of a deed, up from 43, as well as 6,541 cash sales and 4,987 swaps. [Three-player challenger](reports/multiplayer-challenger-3.json) · [Four-player challenger](reports/multiplayer-challenger-4.json) · [Replayed Collector games](reports/four-trained-collectors.json)

Each player starts from the same frozen adaptive v4 and learns a 24-parameter neural network that changes purchases, bids, trade proposals, partner selection and replies. Its inputs include each rival’s holdings in a color group, the largest opposing rent, available cash, and how a proposed deal affects other players’ prospects for completing a set. The two action-specific outputs share seven hidden units. Building, jail and liquidation use v4. The local simulator enforces the true ownership and shared building stock, allows one deed each way plus cash per offer, and conducts auctions using cash-backed maximum bids.

Evolutionary training scored each policy by how often it was the last solvent player. Each format evaluated 32 policies per generation for 24 generations against basic bots, earlier neural models, blockers, perturbed multiplayer bots and earlier training opponents. The searches played **502,272 three-player** and **500,736 four-player** games. Policies shared 9,144 independently seeded starts across both searches and played each seat. Another **75,600 games** selected the two finalists before qualification. Every game still open after 1,000 individual turns counted as a non-win. [Three-player training](reports/multiplayer-training-3.json) · [Four-player training](reports/multiplayer-training-4.json)

The local tournament commands select the checkpoint for the specified table size. Use `--model adaptive-v4` for the original duel-trained strategy.

```sh
./monopoly three-player --opponents builder cautious --boards 600
./monopoly four-player --opponents collector builder cautious --boards 600
```

To reproduce both searches, run the commands below with a C++17 compiler, Make, and Python 3.9 or newer. The trainer writes a candidate under `build/multiplayer-training/` and saves the win counts and weights from every generation under `reports/`. Add `--resume` to the same training command after an interruption.

```sh
for seats in 3 4; do
  ./monopoly train-multiplayer --seats "$seats" --games 500000 --rounds 24 --population 32 --validation-boards 1200 --finalists 8
  python3 multiplayer_qualify.py --seats "$seats" --candidate "build/multiplayer-training/candidate-$seats.json" --report "reports/multiplayer-qualification-$seats.json"
done
```

## Four-player baseline before training

V4 played 2,400 four-player games per lineup. Each game used one shared board and continued through bankruptcies until one player remained or 1,000 individual turns elapsed. V4 played all four starting seats on each of 600 independently seeded boards. Capped games counted as non-wins.

| Three opponents | V4 wins | Win rate | Approximate 95% interval | Games reaching cap |
| --- | ---: | ---: | ---: | ---: |
| Three Collectors | 860 | 35.83% | 33.87–37.79% | 0 |
| Collector, Builder, Cautious | 1,041 | 43.38% | 41.38–45.37% | 0 |
| Earlier neural checkpoints v1, v2, v3 | 797 | 33.21% | 31.32–35.09% | 19 |

Against three Collectors, opponents accepted 10,259 of V4’s 18,522 offers. V4 swapped deeds 5,733 times, sold a deed for cash 4,483 times, and bought a deed for cash 43 times. Swaps could include cash. The Collectors also completed 6,904 trades with one another. Each report records the checkpoint components, results by seat, negotiations, and an interval accounting for the four games sharing each start. [Three Collectors](reports/four-collectors.json) · [Mixed basic bots](reports/four-mixed.json) · [Earlier neural bots](reports/four-neural.json)

With trading disabled for everyone on the same boards, V4 won 107 games against three Collectors (4.46%) and 352 against the mixed basic bots (14.67%). Respectively, 1,635 and 1,423 games still had multiple players at the turn limit. These rates measure outright wins within the time allowed. [Three Collectors without trading](reports/four-collectors-no-trades.json) · [Mixed table without trading](reports/four-mixed-no-trades.json)

All four seats can buy, bid, build, go bankrupt, and negotiate with any surviving player. An active player may send one offer per turn with up to one deed per side plus cash. Auctions use cash-backed maximum bids. The frozen network weights were trained in duels. To evaluate a proposal, the bot sees its own holdings and the selected rival’s holdings. The other two seats’ properties appear unowned in that evaluation. The simulator tracks the true owner of every property and validates each transaction. The reported rates describe this local format.

```sh
./monopoly four-player --model adaptive-v4 --opponents collector builder cautious --boards 600
```

## Try a negotiation

Build once with a C++17 compiler and Make on macOS or Linux. Playing requires Python 3.9 or newer and the standard library.

```sh
make
./monopoly example offer | ./monopoly bot
./monopoly example trade | ./monopoly assess
./monopoly example bundle-offer | ./monopoly bot
./monopoly example bundle-trade | ./monopoly assess
```

The examples cover a single-deed negotiation and a package involving multiple deeds, cash and a held jail card. The assessment returns the decision, both players’ predicted scores, and the estimated willingness of the recipient. The package assessment also itemizes interest and post-trade holdings. The host opts into package trading on its decision packets. [Decision contract and worked examples](COMPETITION.md)

Play a local match with the previous champion as the opponent.

```sh
./monopoly duel --games 4 -- ./monopoly bot --model legacy
```

## Enter the competition

Your friend's website controls the dice, decks, turns, and game results. Our player runs locally. If the site adopts the included polling protocol, it supplies two URLs and an optional bearer token. If it already uses a different protocol, its adapter can pass public game states to the same bot through a persistent JSON Lines process. The integration guide includes the message schema, move codes, a worked example, and the host's acknowledgement format. [Competition integration guide](COMPETITION.md)

```sh
./monopoly connect --poll-url "$POLL_URL" --submit-url "$SUBMIT_URL"
```

For bearer authentication, set the token through the environment and add `--token-env MONOPOLY_TOKEN`. The organizer's live API and rival bot are still pending.

For a linked series that permits learning between games, add `--adapt-series` and have the host supply `previous_game_id`, `opponent_id` and `previous_winner`. The process uses reported wins to choose a neural checkpoint for the next game. Normal single-game play still updates the opponent estimate after observed actions. [Host fields and examples](COMPETITION.md)

On three challengers trained after all four available checkpoints were frozen, the rematch learner won **37.75%** across 4,320 games, compared with **37.45%** for fixed v4 on those boards. The paired 95% intervals included zero for each rival. Against a changing archived rival, the learner moved away from a struggling checkpoint over subsequent games, but fixed v4 still won more often across that series. Fixed v4 is the default. [Fresh series evaluation](reports/series-generalization.json) · [Archived and switching opponents](reports/series-learning.json)

## Models and training

The board network has 128 inputs, hidden layers of 48 and 24 ReLU units, and a scalar output for each seat. It scores both seats with shared weights and converts their difference into complementary estimates. Its 7,392 weights were learned through backpropagation on **1,000,000 new simulated games**. One public position was sampled per game, with victory worth one, defeat zero, and a capped game one half. Training behavior mixed the previous strategies, perturbed opponents, and players that refused to trade. An additional 20,000 games selected and calibrated the checkpoint. An independent 40,000-game validation measured how well it predicted outcomes. [Value training report](reports/neural-training.json)

The response network has 36 offer and position features, a 24-unit ReLU layer, and a sigmoid output. It trained on **583,086 legal offer responses** from 50,000 simulated games. The generator mixed strategic proposals and randomized legal terms. The test split contained 59,106 responses from 5,000 different games. Its log loss was 0.366 versus 0.657 for always predicting the training acceptance rate. These estimates describe simulated players. Actual rivals can have different negotiating policies. [Response training report](reports/response-training.json)

For decisions such as buying property, bidding, raising cash, and building, the bot constructs plausible resulting positions and scores them with the value network. Earlier strategic weights provide action priors. Negotiation uses legal candidate generation, the response model, and position values to choose terms. Incoming offers compare the deal with keeping the current position, with simulated continuations for close calls. The engine validates the move before changing ownership or collecting money. [Research behind the design](RESEARCH.md)

The recurrent opponent model trained on 1,083,664 observed actions from 24,000 simulated games. Each input window contains up to 16 public actions, such as accepting a deal or winning a deed auction at a visible price. Its two outputs estimate blanket trade refusal and aggressive acquisition. During a match, the weights stay fixed while the observed sequence changes. The resulting estimates can reverse when the rival changes course. A separate population search trained the 20 auction and financing parameters over 1,000,000 more matches. The player combines those parameters with board-network values, recent auction prices and simulated legal alternatives. [Opponent training](reports/opponent-training.json) · [Acquisition training](reports/specialist-training.json)

## Historical results for adaptive v1

We played the frozen and adaptive versions on the same 1,000 fresh seeds per opponent. Each seed was played from both seats, for 2,000 games per entrant per opponent and 44,000 games overall. Each rate is outright wins divided by all games. The 1,000-turn cap counts as a non-win.

| Opponent | Earlier neural player | Adaptive player | Adaptive wins / 2,000 |
| --- | ---: | ---: | ---: |
| Collector | 93.15% | 95.20% | 1,904 |
| Builder | 89.10% | 92.05% | 1,841 |
| Cautious | 90.15% | 89.95% | 1,799 |
| Previous rollout champion | 63.20% | 61.20% | 1,224 |
| Known training blocker | 44.10% | 77.45% | 1,549 |
| Previous neural player | 49.90% | 54.80% | 1,096 |
| Starts trading, then refuses | 82.35% | 84.70% | 1,694 |
| Starts refusing, then trades | 94.15% | 96.95% | 1,939 |
| Temporarily refuses after bad offers | 89.70% | 90.95% | 1,819 |
| New independent no-trade bidder | 72.40% | 73.75% | 1,475 |
| Challenger trained against this entrant | 44.60% | 48.10% | 962 |

The bidder model includes the known blocker’s pricing logic as one candidate explanation of observed bids. That blocker also participated in training. The cross-game challenger chose six bidding, reserve, management and trading parameters from 4,416 fresh games specifically against the locked entrant. The challenger then played the qualification seeds with its parameters fixed. Our 48.10% against it had a paired-seed 95% interval of 45.94%–50.26%. [Full qualification results](reports/adaptive-evaluation.json) · [Challenger training](reports/qualification-rival.json)

A separate 12,000-game experiment kept the adaptive policy and its weights fixed and removed its public behavioral history. Restoring history improved the outright win rate by 14.3 percentage points against the trained challenger and 4.7 points against the independent bidder. Across three strategy-switching fixtures, the estimated change was +0.07 points (95% interval −1.19 to +1.32). Both neural opponent estimates and auction-price evidence use this history. [Matched history experiment](reports/history-ablation.json)

## Historical results for adaptive v2

We also trained a second candidate, available with `--model adaptive-v2`. It uses an expanded model of rival bids when public actions conflict with the known blocker’s bidding or trading policy. It practiced against the first challenger and several related opponents. The first adaptive model was the reference for this experiment.

We locked both players and trained a new six-parameter rival for 4,416 games against the second candidate. We then played 2,000 games per player per opponent across 13 fixtures (52,000 games), using new board seeds and both starting seats. The old challenger and the newly trained challenger played with their respective learned parameters fixed throughout this qualification.

| Opponent | Default | Second candidate |
| --- | ---: | ---: |
| First challenger, used during candidate training | 45.60% | 59.60% |
| New challenger trained against the second candidate | 46.05% | 38.15% |
| Known trade-refusing blocker | 77.85% | 77.85% |
| Independent no-trade bidder | 70.45% | 70.55% |
| Old rollout champion | 61.25% | 59.20% |

Against the new challenger the second candidate lost 7.90 percentage points relative to the default (95% interval for the paired difference −10.65 to −5.15 points). It beat the default head-to-head in 56.70% of games. The matchup reversal kept the second candidate from replacing v1 at the time. In separate development games, a bidder using an additive pricing formula beat both models. Their outright win rates were 44.46% for the default and 42.29% for the candidate. The organizer's entrant and rules have yet to be tested. [Full second qualification](reports/adaptive-v2-evaluation.json) · [Candidate training](reports/resilience-training.json) · [New challenger](reports/qualification-rival-v2.json) · [Candidate history experiment](reports/history-ablation-v2.json)

## Earlier two-network player

The earlier player (`--model frozen`) played 5,000 fresh games per opponent, with 2,500 starts in each seat. The metric is outright wins divided by all games, including those that reached 1,000 turns. The table gives two-sided Wilson intervals.

| Opponent | Wins | Losses | Capped | Win rate | 95% interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous champion | 3,025 | 1,971 | 4 | 60.50% | 59.14%–61.85% |
| Independent blocker | 2,290 | 2,477 | 233 | 45.80% | 44.42%–47.18% |
| Collector | 4,657 | 343 | 0 | 93.14% | 92.41%–93.81% |
| Builder | 4,529 | 471 | 0 | 90.58% | 89.74%–91.36% |
| Cautious | 4,521 | 479 | 0 | 90.42% | 89.57%–91.21% |
| Identical neural player | 2,500 | 2,488 | 12 | 50.00% | 48.61%–51.39% |

Across those 30,000 games, the neural entrant proposed 352,708 legal deals. Opponents accepted 56,327. The entrant accepted 7,768 incoming offers and declined 91,619. The blocker refused every trade. The previous champion won 43.33% against the blocker in its earlier benchmark. The competition target of 99% remains open, and the organizer's actual entrant has yet to play. [Full match report and trade counts](reports/neural-evaluation.json)


## Reproduce and verify

```sh
make test
make test-http
./monopoly train --directory /tmp/monopoly-trained
./monopoly evaluate --games 5000 --model /tmp/monopoly-trained/adaptive.json --report /tmp/monopoly-results.json
python3 league_evaluate.py --model models/adaptive-v4.json --seed 481297157 --rival-seed 816143393 --games 2000 --report /tmp/qualification.json --rival-checkpoint /tmp/qualification-rival.json
```

Training also requires NumPy. Set `MONOPOLY_TRAIN_PYTHON` to the Python executable containing NumPy if needed. The default training directory is `build/trained-player/`. Training proceeds through board and response networks, opponent modeling, bidding, trade evaluation, and the pressure policy. The published files let the bot play immediately. Use `train-v1`, `train-candidate`, `train-league` or `train-pressure` to reproduce a specific adaptive generation, and `train-neural` for the two-network baseline. The HTTP test plays to bankruptcy, exchanges package requests, and retries a lost acknowledgement.

The previous champion remains available as `--model legacy`. Its parameter-search training and benchmark can be inspected separately, and `./monopoly train-legacy` reproduces that trainer. [Previous checkpoint](models/champion.json) · [Previous match report](reports/evaluation.json)

## Rules and match conventions

The simulator follows the 2017 US Classic board, traditional Chance and Community Chest decks, and the two-player bankruptcy win condition. It implements shuffled decks, doubles, jail and release cards, taxes, 28 title deeds, rent, auctions, voluntary and forced mortgages, even development, and the shared supply of 32 houses and 12 hotels. [Hasbro C1009 rules](https://www.hasbro.com/common/documents/dad288661c4311ddbd0b0800200c9a66/97DF546B6BF64A70A2A759D3FA5BD804.pdf)

The competition format gives each seat a management window before each roll, the active player one offer per turn, and a base trade format of up to one deed per player plus cash. The package extension permits multiple deeds and held release cards. Auction participants submit maximum bids backed by cash on hand. In an unresolvable hotel downgrade during a house shortage, a player can liquidate the group's buildings at half cost. The benchmark stops a game after 1,000 individual turns and records it as unresolved. These conventions matter when comparing results from another host. [Full decision contract](COMPETITION.md)
