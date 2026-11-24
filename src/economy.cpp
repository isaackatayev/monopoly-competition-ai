#include "groups.hpp"

#include <cstdint>

namespace mono {
namespace {
bool valid_deed(int square) {
    return square >= 0 && square < Squares && board[square].group >= 0;
}

bool owns(const State& s, int who, int square) {
    return (who == 0 || who == 1) && valid_deed(square) && s.owner[square] == who;
}

bool has_buildings(const State& s, int group) {
    if (group >= Rail) return false;
    for (int i = 0; i < group_size[group]; ++i)
        if (s.level[group_squares[group][i]]) return true;
    return false;
}

int interest(int square) { return (board[square].mortgage + 9) / 10; }
int redemption(int square) { return board[square].mortgage + interest(square); }

void transfer(State& s, int square, int new_owner) {
    const int old = s.owner[square], group = board[square].group;
    if (old >= 0) {
        --s.count[old][group];
        s.mort_count[old] -= s.mort[square];
    }
    if (new_owner >= 0) {
        ++s.count[new_owner][group];
        s.mort_count[new_owner] += s.mort[square];
    } else {
        s.mort[square] = false;
    }
    s.owner[square] = static_cast<int8_t>(new_owner);
}

void build(State& s, int who, int square, int price) {
    if (++s.level[square] == 5) {
        --s.hotels;
        s.houses += 4;
    } else {
        --s.houses;
    }
    s.p[who].cash -= price;
}

void sell(State& s, int who, int square) {
    const int price = board[square].house / 2;
    if (s.level[square] == 5 && s.houses < 4) {
        // The bank cannot make change for a hotel. Sell the entire group's
        // improvements together at half cost, keeping the group even.
        const int g = board[square].group;
        for (int i = 0; i < group_size[g]; ++i) {
            const int deed = group_squares[g][i], level = s.level[deed];
            s.p[who].cash += level * price;
            if (level == 5) ++s.hotels;
            else s.houses += level;
            s.level[deed] = 0;
        }
        return;
    }
    if (s.level[square]-- == 5) {
        ++s.hotels;
        s.houses -= 4;
    } else {
        ++s.houses;
    }
    s.p[who].cash += price;
}

bool raise_cash(State& s, int who, const Action& action) {
    const int square = action.square;
    if (action.value == Mortgage && can_mortgage(s, who, square)) {
        s.mort[square] = true;
        ++s.mort_count[who];
        s.p[who].cash += board[square].mortgage;
        return true;
    }
    if (action.value == Sell && can_sell(s, who, square)) {
        sell(s, who, square);
        return true;
    }
    return false;
}

void redeem(State& s, int who, int square) {
    s.p[who].cash -= redemption(square);
    s.mort[square] = false;
    --s.mort_count[who];
}

// Each player submits an ascending-auction ceiling. The winner pays the
// minimum legal winning amount. Random priority resolves equal ceilings.
struct Sale { int winner = -1, price = 0; };
Sale auction(Game& game, const int (&squares)[2], int amount) {
    constexpr int minimum = 10;
    int bids[2];
    for (int who = 0; who < 2; ++who) {
        auto action = game.ask_player(who, {Kind::Bid, squares[who], amount});
        bids[who] = std::clamp(action.value, 0, game.s.p[who].cash);
    }
    if (std::max(bids[0], bids[1]) < minimum) return {};
    const int winner = bids[0] == bids[1] ? game.rng.below(2) : int(bids[1] > bids[0]);
    const int loser_bid = bids[1 - winner];
    int price = minimum;
    if (loser_bid >= minimum)
        price = loser_bid + (loser_bid < bids[winner]);
    return {winner, price};
}

// Returns true if a building changed hands.
bool buy_building(Game& game, int who, int square) {
    auto& s = game.s;
    const bool hotel = s.level[square] == 4;
    const int opponent = 1 - who;
    if ((hotel ? s.hotels : s.houses) == 1) {
        // Ask only when the other seat has an eligible competing build.
        bool eligible = false;
        for (int g = 0; g < Rail && !eligible; ++g) {
            if (s.count[opponent][g] != group_size[g]) continue;
            for (int i = 0; i < group_size[g]; ++i) {
                int alternative = group_squares[g][i];
                if ((s.level[alternative] == 4) == hotel && can_build(s, opponent, alternative)) {
                    eligible = true;
                    break;
                }
            }
        }
        if (eligible) {
            // A table participant can see which type of building is contested.
            int amount = game.table_observer ? (hotel ? -2 : -1) : 0;
            Action other = game.ask_player(opponent, {Kind::Manage, -1, amount});
            if (other.value == Build && can_build(s, opponent, other.square)
                    && (s.level[other.square] == 4) == hotel) {
                int squares[2];
                squares[who] = square;
                squares[opponent] = other.square;
                auto sale = auction(game, squares, hotel ? -2 : -1);
                if (sale.winner < 0) return false;
                build(s, sale.winner, squares[sale.winner], sale.price);
                return true;
            }
        }
    }
    build(s, who, square, board[square].house);
    return true;
}

bool tradable(const State& s, int who, int square) {
    return square == -1 || (owns(s, who, square) && !has_buildings(s, board[square].group));
}

void settle_interest(State& s, const Policy& pol, int who, int square) {
    if (square < 0 || !s.mort[square]) return;
    if (can_redeem(s, who, square)) {
        const auto a = ask(pol,s,who,{Kind::Manage, square, redemption(square)});
        if (a.value == Redeem && a.square == square) {
            redeem(s, who, square);
            return;
        }
    }
    s.p[who].cash -= interest(square);
}
} // namespace

bool can_build(const State& s, int who, int square) {
    if (!owns(s, who, square)) return false;
    const auto& deed = board[square];
    const int group = deed.group, level = s.level[square];
    if (group >= Rail || s.count[who][group] != group_size[group] || level >= 5
        || s.p[who].cash < deed.house || (level == 4 ? s.hotels : s.houses) == 0) return false;
    for (int i = 0; i < group_size[group]; ++i) {
        const int peer = group_squares[group][i];
        if (s.level[peer] < level || s.mort[peer]) return false;
    }
    return true;
}

bool can_sell(const State& s, int who, int square) {
    if (!owns(s, who, square) || s.level[square] == 0) return false;
    const int group = board[square].group;
    for (int i = 0; i < group_size[group]; ++i)
        if (s.level[group_squares[group][i]] > s.level[square]) return false;
    return true;
}

bool can_mortgage(const State& s, int who, int square) {
    return owns(s, who, square) && !s.mort[square] && !has_buildings(s, board[square].group);
}

bool can_redeem(const State& s, int who, int square) {
    return owns(s, who, square) && s.mort[square] && s.p[who].cash >= redemption(square);
}

int rent(const State& s, int square, int dice, int special) {
    if (!valid_deed(square) || s.owner[square] < 0 || s.mort[square]) return 0;
    const auto& deed = board[square];
    const int group = deed.group, count = s.count[s.owner[square]][group];
    if (group == Rail) return (25 << (count - 1)) * (special == 1 ? 2 : 1);
    if (group == Utility) return dice * (special == 2 || count == 2 ? 10 : 4);
    int level = s.level[square];
    return deed.rents[level] * (level == 0 && count == group_size[group] ? 2 : 1);
}

void Game::property(int who, int square, int dice, int special) {
    if (!valid_deed(square)) return;
    const int owner = s.owner[square];
    if (owner >= 0) {
        if (owner != who) {
            int payment = rent(s, square, dice, special);
            if (payment) observe(TableEvent::Rent, who, owner, payment);
            pay(who, owner, payment);
        }
        return;
    }
    const int price = board[square].price;
    if (s.p[who].cash >= price && ask_player(who, {Kind::Buy, square, price}).value == 1) {
        s.behavior[who].append({BoughtAtFace,square,price,s.turn});
        s.p[who].cash -= price;
        transfer(s, square, who);
    } else {
        if (s.p[who].cash >= price) s.behavior[who].append({DeclinedFace,square,price,s.turn});
        const int squares[] = {square, square};
        const auto sale = auction(*this, squares, 10);
        if (sale.winner >= 0) {
            s.behavior[sale.winner].append({WonAuction,square,sale.price,s.turn});
            s.p[sale.winner].cash -= sale.price;
            transfer(s, square, sale.winner);
        }
    }
}

bool Game::pay(int who, int creditor, int amount) {
    if (!amount) return true;
    while (s.p[who].cash < amount) {
        auto a = ask_player(who, {Kind::Liquidate, -1, amount});
        if (raise_cash(s, who, a)) continue;
        // An invalid/pass choice cannot turn a solvent debtor into a bankrupt
        // player. Mortgage an unimproved deed or sell one legal building.
        bool raised = false;
        for (int sq = 0; sq < Squares && !raised; ++sq) {
            if (s.owner[sq] != who) continue;
            raised = raise_cash(s, who, {Mortgage, sq}) || raise_cash(s, who, {Sell, sq});
        }
        if (raised) continue;
        // Head-to-head bankruptcy ends play immediately. No subsequent
        // bank auctions or mortgage elections can affect the winner.
        if (creditor >= 0) {
            s.p[creditor].cash += s.p[who].cash;
            s.p[creditor].cards |= s.p[who].cards;
            s.p[who].cards = 0;
        } else {
            for (int d = 0; d < 2; ++d)
                if (s.p[who].cards & (1u << d)) return_card(who, d);
        }
        for (int sq = 0; sq < Squares; ++sq)
            if (s.owner[sq] == who) transfer(s, sq, creditor);
        s.p[who].cash = 0;
        s.winner = 1 - who;
        return false;
    }
    s.p[who].cash -= amount;
    if (creditor >= 0) s.p[creditor].cash += amount;
    return true;
}

void Game::manage(int who) {
    // A finite transaction window also bounds a malfunctioning external
    // policy repeatedly mortgaging/redeeming the same property.
    for (int i = 0; i < 128; ++i) {
        const auto a = ask_player(who, {Kind::Manage});
        if (a.value == Build && can_build(s, who, a.square)) {
            if (!buy_building(*this, who, a.square)) break;
        } else if (a.value == Redeem && can_redeem(s, who, a.square)) {
            redeem(s, who, a.square);
        } else if (!raise_cash(s, who, a)) {
            break;
        }
    }
}

bool legal_trade(const State& s, int who, const Action& a) {
    if (who != 0 && who != 1) return false;
    const int other = 1 - who;
    if ((a.give == -1 && a.take == -1) || !tradable(s, who, a.give)
        || !tradable(s, other, a.take)) return false;
    const int my_fee = (a.take >= 0 && s.mort[a.take]) ? interest(a.take) : 0;
    const int their_fee = (a.give >= 0 && s.mort[a.give]) ? interest(a.give) : 0;
    return int64_t(s.p[who].cash) - a.cash >= my_fee && int64_t(s.p[other].cash) + a.cash >= their_fee;
}

void settle_trade(State& s, int who, const Action& a, const Policy& proposer, const Policy& recipient) {
    const int other = 1 - who;
    s.p[who].cash -= a.cash;
    s.p[other].cash += a.cash;
    if (a.give >= 0) transfer(s, a.give, other);
    if (a.take >= 0) transfer(s, a.take, who);
    settle_interest(s, proposer, who, a.take);
    settle_interest(s, recipient, other, a.give);
}

void Game::trade(int who) {
    const int other = 1 - who;
    const auto a = ask_player(who, {Kind::Offer});
    if (!legal_trade(s,who,a)) return;
    ++offers_made[who];
    BehaviorEvent event{ProposedTrade, a.give >= 0 ? a.give : a.take, a.cash, s.turn,
                        a.give, a.take, offer_generosity(s,who,a)};
    s.behavior[who].append(event);
    const Query offer{Kind::Accept, -1, 0, a.give, a.take, a.cash};
    if (ask_player(other, offer).value != 1) {
        event.kind = DeclinedTrade;
        s.behavior[other].append(event);
        s.rejected[who]={a.give,a.take,a.cash,s.turn};
        return;
    }
    event.kind = AcceptedTrade;
    s.behavior[other].append(event);
    ++offers_closed[who];
    s.rejected[0]={};
    s.rejected[1]={};
    settle_trade(s,who,a,*policies[who],*policies[other]);
}
} // namespace mono
