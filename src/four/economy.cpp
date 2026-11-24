#include "game.hpp"
#include "../groups.hpp"

namespace mono::four {
namespace {
bool deed(int square) {
    return square >= 0 && square < Squares && board[square].group >= 0;
}

int fee(int square) { return (board[square].mortgage + 9) / 10; }

struct Sale { int winner = -1, price = 0; };

// Each eligible seat supplies its ceiling for the ascending auction. With
// tied ceilings the random priority determines who reached that price first.
Sale auction(Game& game, const std::array<int, Seats>& targets, int amount) {
    constexpr int floor = 10;
    std::array<int, Seats> leaders{};
    int highest = 0, second = 0, lead_count = 0;
    for (int seat = 0; seat < Seats; ++seat) {
        if (!game.alive[seat] || targets[seat] < 0) continue;
        const int offered = game.ask(seat, {Kind::Bid, targets[seat], amount}).value;
        const int bid = std::clamp(offered, 0, game.players[seat].cash);
        if (bid < floor) continue;
        if (bid > highest) {
            second = highest;
            highest = bid;
            lead_count = 0;
        } else if (bid < highest) {
            second = std::max(second, bid);
            continue;
        } else {
            second = highest;
        }
        leaders[lead_count++] = seat;
    }
    if (lead_count == 0) return {};
    const int winner = leaders[lead_count == 1 ? 0 : game.rng.below(lead_count)];
    return {winner, second < floor ? floor : second + (second < highest)};
}

void build(Game& game, int who, int square, int price) {
    if (++game.level[square] == 5) {
        --game.hotels;
        game.houses += 4;
    } else {
        --game.houses;
    }
    game.players[who].cash -= price;
}

bool buy_building(Game& game, int who, int square) {
    const bool hotel = game.level[square] == 4;
    if ((hotel ? game.hotels : game.houses) == 1) {
        std::array<int, Seats> targets;
        targets.fill(-1);
        targets[who] = square;
        int bidders = 1;
        for (int other = 0; other < Seats; ++other) {
            if (other == who || !game.alive[other]) continue;
            const State state = game.view(other, who);
            bool eligible = false;
            for (int sq = 0; sq < Squares && !eligible; ++sq)
                eligible = (game.level[sq] == 4) == hotel && can_build(state, 0, sq);
            if (!eligible) continue;
            const Action choice = game.ask(other, {Kind::Manage});
            if (choice.value == Build && can_build(state, 0, choice.square)
                    && (game.level[choice.square] == 4) == hotel) {
                targets[other] = choice.square;
                ++bidders;
            }
        }
        if (bidders > 1) {
            const Sale sale = auction(game, targets, hotel ? -2 : -1);
            if (sale.winner < 0) return false;
            build(game, sale.winner, targets[sale.winner], sale.price);
            return true;
        }
    }
    build(game, who, square, board[square].house);
    return true;
}

void sell(Game& game, int who, int square) {
    const int price = board[square].house / 2;
    if (game.level[square] == 5 && game.houses < 4) {
        // No houses to make change for a hotel. Sell the group's buildings.
        const int group = board[square].group;
        for (int i = 0; i < group_size[group]; ++i) {
            const int sq = group_squares[group][i], level = game.level[sq];
            game.players[who].cash += level * price;
            if (level == 5) ++game.hotels;
            else game.houses += level;
            game.level[sq] = 0;
        }
    } else {
        if (game.level[square]-- == 5) {
            ++game.hotels;
            game.houses -= 4;
        } else {
            ++game.houses;
        }
        game.players[who].cash += price;
    }
}

bool raise_cash(Game& game, const State& state, int who, Action action) {
    const int square = action.square;
    if (action.value == Mortgage && can_mortgage(state, 0, square)) {
        game.mort[square] = true;
        game.players[who].cash += board[square].mortgage;
        return true;
    }
    if (action.value != Sell || !can_sell(state, 0, square)) return false;
    sell(game, who, square);
    return true;
}

// Mandatory interest has been paid for every inherited mortgage. Present
// this deed's full immediate redemption price, including the prepaid amount.
void offer_redemption(Game& game, int who, int square) {
    const int principal = board[square].mortgage, interest = fee(square);
    if (!game.mort[square] || game.players[who].cash < principal) return;
    Query query{Kind::Manage, square, principal + interest};
    State state = game.view(who, game.focus(who, query));
    state.p[0].cash += interest;
    const Action choice = mono::ask(game.policies[who], state, 0, query);
    if (choice.value == Redeem && choice.square == square) {
        game.players[who].cash -= principal;
        game.mort[square] = false;
    }
}
} // namespace

void Game::transfer(int square, int to) {
    owner[square] = to;
    if (to < 0) mort[square] = false;
}

void Game::auction_deed(int square) {
    if (!deed(square) || owner[square] >= 0 || living() <= 1) return;
    std::array<int, Seats> targets;
    targets.fill(square);
    const Sale sale = auction(*this, targets, 10);
    if (sale.winner < 0) return;
    players[sale.winner].cash -= sale.price;
    transfer(square, sale.winner);
    observe(sale.winner, {WonAuction, square, sale.price, turn});
}

void Game::property(int who, int square, int dice, int special) {
    if (!deed(square) || !alive[who]) return;
    const int landlord = owner[square];
    if (landlord >= 0) {
        if (landlord != who && !mort[square])
            pay(who, landlord, rent(view(who, landlord), square, dice, special));
        return;
    }
    const int price = board[square].price;
    if (players[who].cash >= price) {
        if (ask(who, {Kind::Buy, square, price}).value == 1) {
            players[who].cash -= price;
            transfer(square, who);
            observe(who, {BoughtAtFace, square, price, turn});
            return;
        }
        observe(who, {DeclinedFace, square, price, turn});
    }
    auction_deed(square);
}

void Game::manage(int who) {
    if (!alive[who] || living() <= 1) return;
    const Query query{Kind::Manage};
    // Limit a policy that repeatedly mortgages and redeems the same deed.
    for (int i = 0; i < 128; ++i) {
        const int other = focus(who, query);
        const State state = view(who, other);
        const Action choice = mono::ask(policies[who], state, 0, query);
        if (choice.value == Build && can_build(state, 0, choice.square)) {
            if (!buy_building(*this, who, choice.square)) return;
        } else if (choice.value == Redeem && can_redeem(state, 0, choice.square)) {
            mort[choice.square] = false;
            players[who].cash -= board[choice.square].mortgage + fee(choice.square);
        } else if (!raise_cash(*this, state, who, choice)) {
            return;
        }
    }
}

void Game::mortgage_election(int who, int square) {
    if (!deed(square) || owner[square] != who || !mort[square] || !alive[who] || living() <= 1) return;
    if (pay(who, -1, fee(square)) && living() > 1) offer_redemption(*this, who, square);
}

bool Game::pay(int who, int creditor, int amount) {
    if (!alive[who]) return false;
    if (amount <= 0 || creditor == who) return true;
    while (players[who].cash < amount) {
        const Query query{Kind::Liquidate, -1, amount};
        const int other = creditor >= 0 ? creditor : focus(who, query);
        const State state = view(who, other);
        const Action choice = mono::ask(policies[who], state, 0, query);
        if (raise_cash(*this, state, who, choice)) continue;
        bool raised = false;
        for (int sq = 0; sq < Squares && !raised; ++sq) {
            if (owner[sq] == who)
                raised = raise_cash(*this, state, who, {Mortgage, sq})
                      || raise_cash(*this, state, who, {Sell, sq});
        }
        if (raised) continue;

        alive[who] = false;
        if (creditor >= 0) {
            players[creditor].cash += players[who].cash;
            players[creditor].cards |= players[who].cards;
            players[who].cards = 0;
        } else {
            for (int d = 0; d < 2; ++d)
                if (players[who].cards & (1u << d)) return_card(who, d);
        }
        players[who].cash = 0;
        std::array<int, Squares> estate{};
        int size = 0, interest = 0;
        for (int sq = 0; sq < Squares; ++sq) {
            if (owner[sq] != who) continue;
            estate[size++] = sq;
            if (mort[sq]) interest += fee(sq);
            transfer(sq, creditor);
        }
        if (living() <= 1) return false;
        if (creditor < 0) {
            for (int i = 0; i < size; ++i) auction_deed(estate[i]);
        } else if (interest && pay(creditor, -1, interest)) {
            for (int i = 0; i < size; ++i) offer_redemption(*this, creditor, estate[i]);
        }
        return false;
    }
    players[who].cash -= amount;
    if (creditor >= 0) players[creditor].cash += amount;
    return true;
}
} // namespace mono::four
