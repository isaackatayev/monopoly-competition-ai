# Enter the trained bot in a two-player match

The website runs the game. This client receives the public position and the decision due, then returns one move from the trained neural player. The website rolls the dice, controls the decks, validates moves, and determines the winner.

## Run the player

From this directory, build the native library with `make`. Run `./monopoly board` for the canonical 40-space US board, deed prices, mortgages, building costs, and rent schedules. Choose a connection that fits the host.

- **Local process.** Launch `./monopoly bot` once. Write one UTF-8 JSON object and a newline to its standard input for each decision. Read one JSON reply line from standard output. Keep the process open across turns.
- **Remote website.** Run `./monopoly connect --poll-url "$POLL_URL" --submit-url "$SUBMIT_URL"`. Set these shell variables to the player endpoints supplied by the organizer. If the host requires a bearer token, set it in the client environment and add `--token-env MONOPOLY_TOKEN`.

The default player and aliases `champion`, `neural`, and `adaptive` load `models/adaptive.json`. Deploy the full `models/` directory with the bot. Use a version such as `--model adaptive-v3` to pin a checkpoint. The earlier two-network and rollout players remain available as `--model frozen` and `--model legacy`. [Current results](README.md)

### HTTP exchange

| Request | Host response |
| --- | --- |
| Client GETs the poll URL. | Return HTTP 200 and the decision packet, HTTP 204 to wait, or HTTP 200 with `{"status":"complete"}` to end the session. |
| Client POSTs its JSON reply to the submit URL. | Return HTTP 204 to acknowledge, or HTTP 200 with `{"status":"accepted","game_id":"demo","request_id":1}` using the identifiers from that reply. |

Apply each move once, even if a connection breaks after the server receives it. The client repeats the same `Idempotency-Key` for a given protocol and pair of identifiers. The host should store that pair with the accepted move atomically and acknowledge replayed submissions. Use the final endpoint URLs, as the client does not follow redirects. For authenticated play, both endpoints must share an origin and use HTTPS, with HTTP permitted for loopback testing.

To exercise the whole interface locally, run `python3 tests/test_http_game.py --model champion`. It starts a temporary loopback server, plays a game to bankruptcy, retries a lost acknowledgement, and checks every reply against the same policy running locally.

## Try one decision

This example simulates player 0 reaching Oriental Avenue on the first turn. It can be run offline after building the library.

```sh
python3 - <<'PY' | ./monopoly bot
import json
print(json.dumps({
    "protocol": "monopoly.duel.v1", "game_id": "demo", "request_id": 1,
    "player": 0,
    "state": {
        "cash": [1500, 1500], "position": [6, 0], "jail": [-1, -1],
        "cards": [0, 0], "owner": [-1] * 40, "level": [0] * 40,
        "mortgaged": [0] * 40, "houses": 32, "hotels": 12,
        "turn": 1, "active": 0
    },
    "query": {"kind": "buy", "square": 6, "amount": 100}
}))
PY
```

A reply that buys the deed has this shape.

```json
{"protocol":"monopoly.duel.v1","game_id":"demo","request_id":1,"action":{"value":1,"square":-1,"give":-1,"take":-1,"cash":0}}
```

The host applies the move once for that `(game_id, request_id)`, then sends an updated snapshot for the next decision. Either seat may receive a decision during the other seat's turn.

## Request fields

| Field | Meaning |
| --- | --- |
| `protocol` | The string `monopoly.duel.v1`. |
| `game_id`, `request_id` | An integer or a nonempty string of up to 256 characters, chosen by the host and echoed in the reply. Give every decision within a game a unique ID. |
| `player` | Seat being asked to act, 0 or 1. |
| `state` | Full current public position as described below. |
| `query` | One decision. The required `kind` is a lowercase string. Optional `square`, `give`, and `take` default to −1. Optional `amount` and `cash` default to 0. |

State fields use JSON integers, including the 0/1 mortgage flags. Arrays indexed by seat have length 2. Arrays indexed by square have length 40, starting with GO at 0 and ending with Boardwalk at 39.

| State field | Encoding |
| --- | --- |
| `cash[2]`, `position[2]` | Dollars on hand and current board square, respectively. |
| `jail[2]` | −1 when free. In jail, 0, 1, or 2 counts prior failed escape rolls. A visitor on square 10 is free. |
| `cards[2]` | Held Get Out of Jail Free cards as bits. Chance = 1, Community Chest = 2, both = 3, neither = 0. |
| `owner[40]` | −1 for unowned deeds and all non-deed squares, otherwise the owning seat. |
| `level[40]` | 0 for no buildings, 1–4 for houses, 5 for a hotel. |
| `mortgaged[40]` | 1 for a mortgaged deed and 0 otherwise. |
| `houses`, `hotels` | Buildings available to purchase from the bank. A fresh game has 32 and 12. |
| `turn`, `active` | Individual turns begun and seat whose turn is underway. The opening turn is 1. Doubles extend that turn. |
| `last_rejected[2]` (optional) | For each proposer, use `null` or their most recent declined terms as `{"give":27,"take":19,"cash":-90,"turn":25}`. Clear both entries after a deal closes. The neural player uses this public history to improve terms or try a different offer after a refusal. |

## Decision contract

| Query kind | Request supplied by host | Fields to apply from the reply |
| --- | --- | --- |
| `buy` | Unowned deed in `square`, printed purchase price in `amount`. | `value` 1 buys at that price, 0 sends the deed to auction. |
| `bid` | Deed auction uses `square` and `amount` 10. For the final available house or hotel, specify that player's intended build square and `amount` −1 for a house or −2 for a hotel. | `value` is the maximum whole-dollar bid. A value below 10 passes. |
| `manage` | `square` −1 for an ordinary management window. | Read `value` and, for a transaction, `square`. Values are 0 finish, 1 build, 2 sell, 3 mortgage, 4 redeem. Apply one legal action, then ask again with the updated state until finished. |
| `manage` | On receiving a mortgaged deed, send its `square` and the full redemption price in `amount` when affordable. | A 4 targeting the same square redeems immediately. A 0 keeps the mortgage and incurs the transfer interest. |
| `liquidate` | `amount` is the full payment owed before deducting available cash. | Values 2 and 3 sell a building or mortgage a deed. Ask again until enough cash has been raised or the bank has exhausted the legal options. |
| `jail` | Request before rolling a jailed player's dice. | `value` 0 attempts doubles, 1 pays $50, 2 uses the held Chance release card, 3 uses the held Chest release card. |
| `offer` | Ask the prospective proposer. | `give` is their outgoing deed, `take` their requested deed. Positive `cash` is paid by the proposer, negative by the recipient. Both deed fields equal to −1 passes. |
| `accept` | Ask the recipient. Copy the original proposal into `give`, `take`, and `cash`, retaining the proposer's perspective. | `value` 1 accepts the complete proposal, 0 declines. |

## Inspect a trade before submitting it

Pipe any valid incoming `accept` packet into `./monopoly assess`. For a working example, run `./monopoly example trade | ./monopoly assess`. The command prints both players' predicted game scores (the training target gives a capped game half credit) before and after the deal, the modeled chance of acceptance, the proposer's expected gain, and the receiving bot's decision. Use `./monopoly bot` for the reply envelope that goes back to the host.

The response network estimates acceptance using similar positions and terms seen in training. For a new opponent, that estimate can differ from their actual choice. Expected proposer gain multiplies the predicted change in the proposer's win probability by the estimated probability that the other seat accepts. Before sending its leading proposal the bot compares six pairs of simulated 16-turn continuations. It also uses four paired continuations to resolve less decisive incoming offers. The final decision can reflect rent exposure or a chance to develop a set that only appears once play continues.

In the turn-29 sample position, seat 0 owns two orange streets and Ventnor Avenue. Seat 1 owns New York Avenue and the other two yellow streets. Both players would complete a color group by exchanging Ventnor for New York. Running the bundled models gave these decisions.

| Terms from seat 0's perspective | Predicted win probability for seat 1 | Seat 1's decision |
| --- | --- | --- |
| Offer Ventnor for New York and receive $90, the terms emitted by `./monopoly example offer \| ./monopoly bot` | 48.4% before, 30.6% after | Decline |
| Offer Ventnor and pay $75 for New York, the terms in `./monopoly example trade` | 48.4% before, 49.0% after | Accept |

## Rules that affect integration

The training engine uses the two-player US Classic C1009 board, traditional card effects, and the competition decision windows below. Compare these settings with the host's edition before interpreting match scores. [Hasbro Classic rules](https://www.hasbro.com/common/documents/A0AFE3A69EC745EBA77B9A7950BBCA44/AD7742057B1D43609B53D24D75E9CA9B.pdf)

- The sale of a declined deed uses both players' bid ceilings, with $10 as the opening price. A sole bidder pays $10. With two qualifying bids, the higher bidder pays the losing bid plus $1, capped at their own bid. The local simulator chooses a random priority on tied ceilings. The player who declined the printed price can bid. The simulator caps bids at cash on hand.
- Buying and selling buildings proceeds evenly across a complete street color group. Mortgages in the group prevent building. A hotel returns four houses to bank supply. When both players request the last available house or hotel, the mover and the competing builder each bid on their own target square. A shortage that prevents making change for a hotel allows liquidation of the whole group's improvements for half cost in the simulator.
- A mortgage requires an entire color group to be clear of buildings. Redemption costs principal plus 10% interest rounded up to the next dollar. A mortgaged deed collects zero rent. Other deeds retain group-based rent benefits. A recipient keeping a transferred mortgage pays 10% interest immediately and owes the standard full redemption price later.
- The base protocol and original training matches exchange up to one deed from each seat plus signed cash. Hosts that support packages can enable the extension below. Buildings anywhere in a color group block transfers of its deeds. Each recipient must be able to pay their cash obligation and all immediate mortgage interest.
- The simulation requests management from both seats before each roll and accepts one trade proposal per active turn. The trained policy uses the public board and both players' holdings. Provide updated snapshots after purchases, trades, builds, repayments, and forced liquidation. The sender can keep upcoming dice and card order private.

## Multi-deed and Get Out of Jail Free card trades

Add `"capabilities":["multi_deed_trades_v1"]` to the top level of each decision packet where the host can exchange several assets in one transaction. The competition client and `./monopoly bot` accept the same extension. The host owns the board and applies the accepted terms atomically. Classic rules allow players to sell held Get Out of Jail Free cards to each other for an agreed price. A transferred card stays outside its own deck until its new holder plays it. [Hasbro Monopoly rules](https://www.hasbro.com/common/instruct/Monopoly.pdf)

Run these commands from the project directory to request a package proposal, inspect an incoming package and play complete games with the extension enabled.

```sh
./monopoly example bundle-offer | ./monopoly bot
./monopoly example bundle-trade | ./monopoly assess
./monopoly bundle-match --opponent collector --games 20
```

Ask the seller with `"query":{"kind":"offer"}`. The reply gives two lists of board squares, `give_deeds` and `take_deeds`, and two lists of held release cards, `give_cards` and `take_cards`. The card names are `"chance"` and `"chest"`. All terms are from the proposer's perspective. Positive `cash` flows from the proposer to the recipient. Negative `cash` asks the recipient to pay. A pass has `value: 0`, four empty lists and zero cash.

To request the recipient's decision, send the same board with the other `player`, a fresh `request_id`, and the original terms under `query` with `kind: "accept"`. For example, seat 0 can give two deeds and the Chance release card, take a deed and pay $75 in one offer.

```json
{"kind":"accept","give_deeds":[16,27],"take_deeds":[19],"give_cards":["chance"],"take_cards":[],"cash":75}
```

Both deed lists are required if either is present. For a card-only sale, both deed lists may be omitted and either card list may be omitted when empty. The optional scalar `give`, `take`, and `square` must be −1 when supplying lists. An outgoing action can be copied directly into the incoming query. The response echoes the original transaction, `game_id`, and `request_id`. Apply the terms only when the recipient returns `value: 1`. A host can continue to send scalar requests. Single-deed terms encoded as arrays use the same neural assessment and receiving decision as the original format.

`./monopoly assess` reports predicted values for both seats, cash and held cards by seat after the deal, completed street-color groups numbered 0–7, and compulsory mortgage interest. For multi-deed and card offers, the projection keeps incoming mortgages in place and charges their interest immediately. Once a deal is accepted, the host can offer recipients immediate redemption. Charge principal and interest once if redeemed at transfer. If a player keeps a transferred mortgage, charge interest now and the ordinary redemption price if later lifted. Each seat must have enough cash to cover every fee owed at closing.

For incoming terms the evaluator accepts any eligible subset of the 28 deeds and both held release cards. When proposing, it considers deed pairs, triples, whole railroad groups, cash adjustments, and held cards. It scores shortlisted packages with the trained board-value network and tests close incoming decisions with four pairs of 16-turn simulations from the public position. The package acceptance estimate uses a heuristic based on assets, cash and completed groups. The neural value training games and single-deed response training provide no package-specific win-rate or acceptance calibration. Use `proposed_bundle`, `accepted_bundle` or `declined_bundle` in the public history for genuine packages. Record the real cash `amount` and `turn`. Omit `square`, `give` and `take`, or send −1 for each. These events describe the complete offer without selecting one deed to represent it. Optional `generosity` is a dollar estimate of the complete offer's advantage to the recipient. Frequent refusals reduce proposals to one opportunity per eight own turns. Later acceptances can restore regular offers. The host still receives a decision on every incoming offer.

## If the organizer uses another API

Provide the website's player API documentation, one example game state or turn event, and its accepted move or reply format. Identify its rules edition, tie or turn-limit handling, auction format, which decisions it allows, and any authentication or registration required. Supply credentials through the organizer's intended channel.

An adapter can map website player IDs to stable seats 0/1, map properties to `./monopoly board` square numbers, convert the public state, call the bot, and translate the reply into the website's move format. For a live ascending auction, request a maximum bid once for the current position and use legal increments up to that ceiling. Confirm the move was accepted before advancing the state.

## Public opponent history

For the most accurate bids and trade estimates, include `state.behavior` with every decision. It contains two lists indexed by the seat that acted, with at most 16 events each, oldest first. Record a proposal for its sender and an acceptance or refusal for its recipient. All three records keep the original proposer's perspective on deeds and cash. Here is a refusal by seat 1.

```json
{"behavior":[[],[{"kind":"declined_trade","turn":24,"square":27,"give":27,"take":19,"amount":75,"generosity":0}]]}
```

| Event | When to record it | Amount |
| --- | --- | --- |
| `proposed_trade`, `accepted_trade`, `declined_trade` | A legal offer was sent or answered | Cash from proposer to recipient, negative when asking for cash |
| `bought_at_face`, `declined_face` | A player could afford a deed and chose whether to buy | Printed price |
| `won_auction` | Player won a deed auction | Actual price paid |

For trades, `square` is the given deed, or the requested deed when only cash was offered. Use -1 for absent deeds. The simulator computes optional `generosity` from the pre-trade public board, in approximate dollars of recipient benefit. The host can omit it when the original valuation is unavailable. A bidder's private ceiling and the deck order are private game information. For purchases, supply the deed in `square` and omit the trade fields. An empty history gives both opponent estimates a 50% initial value. Inspect a packet with `./monopoly scout`.

Hosts may omit `behavior`. A persistent process then collects incoming proposals, confirmed refusals, deed and jail card transfers, and voluntary purchase declines it can attribute from auction requests. For a package, a same-turn snapshot can confirm acceptance when all deed and card owners match the projected transfer. In the synchronous protocol, an ordinary same-turn management request confirms refusal when all owners still match the original position. Partial transfers or unrelated changes leave the result unknown. A host with asynchronous negotiation should send the explicit event feed. The optional `state.last_rejected` represents a scalar refusal.

For rematches, the host can set optional top-level `opponent_id` on each game and `previous_game_id` on the first request of the new game. Set the latter only for a completed game against the same opponent. A persistent player can carry the rival's six most recent observed trade actions into the opening, even when seats swap. New actions replace these observations. Unlinked games start fresh. This memory is in-process, bounded to 128 recent game/seat pairs. The standard benchmark starts each game fresh.

### Learning across a match series

If the tournament permits learning across games, launch `./monopoly bot --adapt-series` or add `--adapt-series` to `./monopoly connect`. On the **first request of each rematch**, send the completed game's winner as a seat index, or `null` for a tie, along with the public link to that game.

```json
{"game_id":"match-18","opponent_id":"guest-4","previous_game_id":"match-17","previous_winner":1}
```

Those fields accompany the usual decision packet. The player uses its seat in the **previous** game to interpret the result. It selects one neural checkpoint for the entire new game using discounted Thompson sampling. The objective is outright wins, so ties count as non-wins. Outcomes remain in memory while the player process is running. The starting checkpoint plays each unlinked game. Replayed requests return the original move.
