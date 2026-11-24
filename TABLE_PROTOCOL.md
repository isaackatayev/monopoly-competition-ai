# Join a Monopoly Club table

A table runs one two-player game on the host computer. Both seats start open. A person can join from a browser, and an external AI can join through the HTTP API. The host keeps the board, rolls the dice, shuffles the decks, applies your actions and declares the result.

## Start here

**Joining a public table.** Open the full HTTPS invite, including `?table=...`, then select **Take a seat** or leave the page open to watch. An ngrok invite works across networks while the host keeps the game and tunnel running. If ngrok shows a welcome page, choose **Visit Site** to continue. An AI can use an HTTP tool or a Python runner with internet access.

If an HTTP tool receives ngrok’s welcome page when fetching the guide or client, send the request header `ngrok-skip-browser-warning: 1`. [ngrok browser warning](https://ngrok.com/docs/pricing-limits/free-plan-limits#removing-the-interstitial-page)

**Hosting through ngrok.** Start `ngrok http 127.0.0.1:8767 --inspect=false` on the host computer. In a second terminal in the project directory, run `./monopoly table --port 8767 --public-url https://YOUR-NGROK-DOMAIN`, substituting ngrok’s HTTPS forwarding address. Open that address, create a table and use **Invite players** to copy a browser invite or an AI invitation. You can take one seat yourself or host two guests. The match starts when both seats fill.

**Local play.** For this computer, run `./monopoly table` and open the loopback address. For same-network play, run `./monopoly table --lan` and share the LAN invite. Both the player and the host must be able to reach the same local network. The default port is 8765. If you pick another port using `--port`, use the same port for ngrok or LAN connections.

The example player runs with Python 3 and its standard library. Replace the example URL below with the full browser invite copied from your table. Use a separate session file for each table. If you already cloned this project, the example client is in the repository root and you can skip the download.

```sh
invite_url='https://table.example/?table=ABC234'
curl -fsS -H 'ngrok-skip-browser-warning: 1' "${invite_url%/*}/example-agent.py" -o example_table_agent.py
python3 example_table_agent.py "$invite_url" --name Atlas --session atlas-ABC234-seat.json
```

Restart the same command after a disconnect. The private session file remembers the join key. Give each AI its own file. To write a different player, start with `choose_action(packet)` in the example.

### Give another AI these instructions

In the browser, choose **Copy AI invitation** to get the address for your game. For a custom invitation, replace both example URLs in this message.

> Join my Monopoly game at https://table.example/?table=ABC234 and play until you receive the terminal result. Read the guide at https://table.example/protocol. Extract the table ID from the table query parameter. Generate and privately retain a join_key, claim an AI seat, and use its bearer token to poll for decisions. Echo the decision IDs and submit a legal action each time. On HTTP 204, wait for Retry-After and poll again. After a lost acknowledgement, resend the same move. Tell me the winner or draw and the stated reason.

Anyone with the link can watch the game or take an open seat. A join key and a seat token both grant control of the claimed seat. Keep them private. LAN mode uses plain HTTP and is intended for a trusted network. Tables and credentials live in the server process. Restarting the server ends those sessions.

The server retains up to 128 tables. When a new table needs space, the server removes the earliest-created table whose status is `complete` or `error`. If all 128 are waiting or running, the create request returns 503. A retired table's public URL and seat endpoints return 404.

## HTTP endpoints

All paths below use the invite's origin, including its port. POST requests take one UTF-8 JSON object, `Content-Type: application/json` and a `Content-Length` of 1–65,536 bytes. Browser POSTs must use the table's origin. Seat credentials travel in the header `Authorization: Bearer TOKEN`. One program should control a given seat at a time.

| Method and path | Credentials | Result |
| --- | --- | --- |
| `POST /api/tables` | Public | 201 public view of a fresh table. |
| `GET /api/tables/ID` | Public | 200 spectator snapshot, including lobby, board and result. |
| `POST /api/tables/ID/join` | Private join key in body | 200 seat assignment, token and endpoint paths. |
| `GET /api/tables/ID/poll` | Seat token | 200 decision or result, or 204 with an empty body while waiting. |
| `POST /api/tables/ID/submit` | Seat token | 200 acknowledgement of the move. |
| `GET /api/board` | Public | 200 array of all 40 board spaces in position order. |
| `GET /api/host` | Public | 200 object with Boolean `lan_enabled`, array `lan_urls` of same-network origins and `public_url` (configured HTTPS origin or null). |
| `GET /?table=ID` | Public | Browser game and spectator view. |
| `GET /protocol`, `GET /example-agent.py` | Public | This integration guide and the working Python player. |

### Create or watch

Invited players use the existing ID. To host a new table programmatically, POST the following body to `/api/tables`.

```json
{"seats":["open","open"],"turn_cap":500,"seed":12345}
```

Send `{}` for a table with default settings. If you include `seats`, its value must be `["open","open"]`. The optional `turn_cap` accepts 1–1000 individual turns and defaults to 500. Omit `seed` for a server-chosen game or supply a whole number from 0 to 2147483647 for reproducible dice and decks with identical play. A completed table reveals its seed. Copy the six-character uppercase alphanumeric `id` from the response into `/?table=ID` and the API paths.

The public view includes these fields.

| Field | Meaning |
| --- | --- |
| `id`, `turn_cap` | Table identifier and configured maximum individual turns. |
| `status` | `waiting` for players, `running`, `complete`, or `error`. |
| `players` | Two objects with zero-based `seat`, `kind` (`open`, `human`, `ai`) and display `name`. An open seat's name is null. |
| `state` | Public board position using the fields below, or null before play starts. |
| `decision` | Null or an object with `player`, `request_id`, `query`, `options`, `bid` and `trade`. Use authenticated polling for the actionable packet. |
| `events` | Up to 120 activity objects, oldest first, each with integer `id` and `turn`, `seat` (0, 1 or null), machine-readable `kind` and display `text`. Empty before play starts. |
| `dice` | Most recent movement roll as two integers 1–6, or null before the first roll. |
| `winner`, `reason` | Set on completion. A draw has a null winner. |

The following excerpt shows three fields from a spectator snapshot. The public `decision` places `options`, `bid` and `trade` directly alongside `query`. The authenticated poll packet groups those three fields under `legal`.

```json
{
  "dice": [2, 4],
  "decision": {
    "player": 0, "request_id": 7,
    "query": {"kind":"buy","square":6,"amount":100,"give":-1,"take":-1,"cash":0},
    "options": [
      {"label":"Buy for $100","action":{"value":1}},
      {"label":"Send to auction","action":{"value":0}}
    ],
    "bid": null, "trade": null
  },
  "events": [
    {"id":1,"turn":0,"seat":null,"kind":"start","text":"Both players received $1,500."},
    {"id":2,"turn":1,"seat":0,"kind":"roll","text":"Atlas rolled 2 + 4 = 6."},
    {"id":3,"turn":1,"seat":0,"kind":"land","text":"Atlas landed on Oriental Avenue."}
  ]
}
```

Activity IDs increase within a table. Use them to deduplicate overlapping snapshots. The activity kinds are `start`, `roll`, `land`, `card`, `rent`, `jail`, `purchase`, `trade`, `manage`, `complete` and `error`. Use `state` for current assets and balances even if earlier events have rolled out of the activity list.

### Claim a seat

Extract `ID` from the invite. Generate a cryptographically random join key in your own process, for example with Python's `secrets.token_urlsafe(32)`. Store it before the first POST so that you can replay a join after a network failure. POST to `/api/tables/ID/join`.

```json
{"name":"Atlas","kind":"ai","join_key":"REPLACE_WITH_A_PRIVATE_RANDOM_KEY"}
```

The name is 1–32 printable characters after trimming surrounding whitespace. The kind is `ai` (default) or `human`. A join key is 32–128 characters from `A–Z`, `a–z`, `0–9`, `_` and `-`. The server assigns the first available seat. Repeating the same join key, name and kind returns the original assignment, including when the seats are now full. For example, a successful response has this shape.

```json
{"id":"ABC234","seat":0,"token":"SERVER_GENERATED_SECRET","poll_url":"/api/tables/ABC234/poll","submit_url":"/api/tables/ABC234/submit"}
```

Resolve both returned paths against the invite's origin. Keep the token out of browser invite links and activity logs. If your program disconnects, the table waits for its decision indefinitely. Resume using the saved token or repeat the original join to recover it.

### Poll, decide and submit

1. GET the returned `poll_url` with the seat token. On 204, wait the number of seconds in `Retry-After` (currently 1), then poll again. A seat can be idle during the lobby, the other seat's decision, or a server-controlled transition.
2. On a 200 response, first check `status`. A terminal response looks like `{"status":"complete","game_id":"ABC234","winner":1,"reason":"bankruptcy"}`. A game reaching the turn cap ends in a draw with `"winner":null` and `"reason":"turn_limit"`. Stop polling at either result.
3. Otherwise the response is a decision packet. Verify `protocol == "monopoly.duel.v1"`, `game_id == ID` and `player` equals your assigned seat. Use the current packet's `legal` descriptor to select one action. The same pending decision may be delivered more than once.
4. POST an object containing the original `protocol`, `game_id`, `request_id` and your `action` to `submit_url` with your seat token. Read the acknowledgement before choosing another action. Immediately poll again after success.

For example, on a `buy` query for Oriental Avenue (`square: 6`, `amount: 100`), a player accepting the printed price can send the following reply. Use the values from your own packet.

```json
{"protocol":"monopoly.duel.v1","game_id":"ABC234","request_id":7,"action":{"value":1,"square":-1,"give":-1,"take":-1,"cash":0}}
```

The server acknowledges it with `{"status":"accepted","game_id":"ABC234","request_id":7}`. The decision ID identifies one move within this table. Echo it unchanged. On a lost connection before the acknowledgement, retry the **same reply** with the **same token**. An identical accepted move gets the same acknowledgement and applies once. A different move for the accepted ID conflicts.

## Decision packet

| Field | Type and use |
| --- | --- |
| `protocol` | String `monopoly.duel.v1`. |
| `game_id`, `request_id` | Table ID string and integer decision ID. |
| `player` | Seat 0 or 1 asked to act. |
| `state` | Public board state at this decision. |
| `query` | Object with `kind`, plus relevant `square`, `amount`, `give`, `take` and `cash` integers. A square of −1 means absent. |
| `legal` | Object with `options` and descriptors for variable bids or trades when available. |

`legal.options` contains objects with a human-readable `label` and a ready-to-submit `action`. Copy an offered action object directly into the reply. Labels are for display. A policy should use the action fields and query kind. Outside a bid or trade, these choices cover the current legal decision. The server handles forced choices automatically.

| Query kind | How to respond |
| --- | --- |
| `buy` | `query.square` is the unowned deed and `amount` its price. `value:1` buys, `value:0` auctions. |
| `bid` | A deed auction supplies the target square and `amount:10`. A final building auction uses your intended build square and `amount:-1` (house) or `-2` (hotel). Pass with the listed pass option, or send an integer ceiling in `value` from `legal.bid.min` to `legal.bid.max` inclusive. |
| `manage` | Select a listed action. `value:0` finishes, 1 builds, 2 sells a building, 3 mortgages, 4 redeems. A transaction targets the listed `square`. The host can ask again after one transaction. A query targeting an incoming mortgaged deed offers immediate redemption or keeping the mortgage. With `square:-1`, a negative `amount` invites a competing build for the final house (−1) or hotel (−2). |
| `liquidate` | The player owes the full `query.amount`. Choose a listed building sale or mortgage to raise cash. Further choices can follow until the debt is covered or bankruptcy occurs. |
| `jail` | `value:0` tries for doubles, 1 pays $50, 2 uses the Chance release card, 3 uses the Community Chest release card. Choose among the available options. |
| `offer` | Pass using the offered action, or propose up to one outgoing deed, one requested deed and a signed cash adjustment. Details below. |
| `accept` | `query.give`, `take` and `cash` quote the original proposer's terms. `value:1` accepts all of them, `value:0` declines. |
| `roll` | A browser seat can be offered `{"value":0}` to roll. AI seats roll automatically. |

For a variable bid the descriptor is `{"min":10,"max":350}` when the seat can bid up to $350. For an offer, `legal.trade` specifies eligible deed squares in `give` and `take` and the outer bounds `min_cash` and `max_cash`. Nonapplicable descriptors are null. For example, `{"give":27,"take":19,"cash":75}` offers Ventnor Avenue and $75 for New York Avenue. A −1 deed index means that side contributes no deed. A proposal requires at least one deed. Negative `cash` asks the recipient to pay. A mortgage incurs immediate interest, rounded up to 10% of principal. After the cash transfer, each player must still cover interest due on their incoming deed. The server validates these pair-specific limits.

Action fields are integer `value` (default 0), `square`, `give`, `take` (default −1), and `cash` (default 0). Use the action shape for the current query. A table offers the scalar trade format described above. The host gives the active player one trade window per individual turn and both players management opportunities before a roll.

**Competing for the last building.** If a player requests the last available house or hotel and the other player could also use it, the other player receives a `manage` query with `square:-1` and `amount:-1` for a house or `amount:-2` for a hotel. The only legal choices in this window are the listed pass or a listed `{"value":1,"square":S}` for a competing build. Passing lets the original build go ahead at the printed price. Selecting a competing build opens an auction. Each player then receives a `bid` query with that same negative `amount` and *their own* intended build square. Submit the bid ceiling or pass as usual.

### Public position

Arrays indexed by seat have length 2. Those indexed by square have length 40. Use `GET /api/board` once to look up the square numbers, names and prices.

| Field | Encoding |
| --- | --- |
| `cash[2]`, `position[2]` | Dollars available and board square for each player. |
| `jail[2]` | −1 when free. Values 0, 1 or 2 count failed escape rolls. Visiting square 10 alone does not put a player in jail. |
| `cards[2]` | Release-card bitmask. Chance = 1, Community Chest = 2, both = 3, neither = 0. |
| `owner[40]` | Seat 0 or 1 for an owned deed and −1 for unowned or non-deed squares. |
| `level[40]`, `mortgaged[40]` | 0–4 houses or 5 for a hotel. Integer 1 flags a mortgaged deed and 0 means unmortgaged. |
| `houses`, `hotels` | Remaining bank inventory. |
| `turn`, `active` | Individual turns begun and seat moving. Doubles can produce several rolls in one turn. |
| `behavior[2]` | Up to 16 recent public events per acting seat, oldest first. Events contain `kind`, `turn`, `square`, `give`, `take`, `amount`, `generosity`. Purchase kinds are `bought_at_face`, `declined_face` and `won_auction`, with the price paid or declined. The `proposed_trade`, `accepted_trade` and `declined_trade` events keep the proposer's perspective on signed cash and deeds. |
| `last_rejected[2]` (optional) | Per proposer, null or their most recently refused terms with `give`, `take`, `cash`, `turn`. A closed deal clears this history. |

Each board entry has `square`, `name`, `group`, `price`, `mortgage`, `building_cost` and `rents`. Groups 0–7 are brown, light blue, pink, orange, red, yellow, green and dark blue. Group 8 is railroads, 9 is utilities, and −1 is a non-deed square. A street's six rent entries run from undeveloped through hotel.

## Errors and recovery

Errors return JSON `{"error":"description"}`. Use the HTTP status for control flow.

| HTTP | Meaning and recovery |
| --- | --- |
| 400 | Invalid JSON, malformed fields or illegal move. Read the error, poll for the current decision and correct the reply. |
| 401 | Missing or incorrect seat token. Repeat the original join with your saved key to recover the token. |
| 403 | The browser request's origin differs from the table. Open the browser game at the invite origin. |
| 404 | Unknown table or path. Check the invite and whether the server restarted. |
| 409 | Table full, conflicting use of a join key, stale decision, wrong seat or conflicting replay. Check the public table and poll your own seat for current state. |
| 413, 415 | Body too large or wrong media type. Send one compact JSON object using `application/json`. |
| 500 | Server or game error. Check table status and the host process. |
| 503 | All 128 tables are in use. Try creating the table after one completes. A completed table may be retired to make room. |

On a transport interruption, retry a read, the identical join or the identical submission with a short increasing delay. A fresh join key claims a new seat. The public table snapshot is sufficient to restore the display, and re-polling restores the outstanding decision.

## Board and money

Square numbers run from GO at 0 to Boardwalk at 39. Advancing past GO pays $200. Each player starts at GO with $1,500. Dollars, square IDs, seat numbers and building counts are integers. The server handles purchases, rent, taxes, cards, bankruptcy and the public supply of 32 houses and 12 hotels.

An undeveloped street in a complete color group earns double base rent. Buildings go up and come down evenly across the group. A mortgaged deed earns zero rent. A railroad charges $25, $50, $100 or $200 according to the owner's railroad count. A utility uses a fresh dice roll multiplied by four or ten for one or both utilities. Chance cards can change these amounts.

Declining a deed opens an auction for both players. Each submits a cash-backed maximum. A sole qualifying bidder pays $10. When both qualify, the winner pays one dollar more than the other bid, up to the winner's maximum. Tied bids use randomly assigned priority and the winner pays that bid. Accepting a deed at its printed price skips the auction.

The player whose token is moving and the player asked to decide can differ. For example, a player can receive an auction or management decision during the other player's turn. Use the decision's seat to decide when to act.
