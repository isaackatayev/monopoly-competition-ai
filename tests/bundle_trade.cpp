#include "bundle_trade.hpp"
#include <cassert>
#include <vector>

namespace {
using namespace mono;
uint64_t bit(int sq) { return uint64_t{1} << sq; }
struct Election {
    std::vector<int> deeds, cash, price;
};
Action redeem_received(void* context,const State& s,int seat,const Query& q) {
    assert(q.kind == Kind::Manage && q.square >= 0);
    assert(s.owner[q.square] == seat && s.mort[q.square]);
    auto& e = *static_cast<Election*>(context);
    e.deeds.push_back(q.square);
    e.cash.push_back(s.p[seat].cash);
    e.price.push_back(q.amount);
    return {Redeem,q.square};
}
}

void test_bundle_trade() {
    Election seen[2];
    Policy seats[2];
    for (int seat=0; seat<2; ++seat) {
        seats[seat].external = redeem_received;
        seats[seat].context = &seen[seat];
    }
    Game game(7092,seats[0],seats[1]);
    auto& s = game.s;
    for (int sq : {1,3,5}) {
        const int owner = sq == 5;
        s.owner[sq] = owner;
        ++s.count[owner][board[sq].group];
        s.mort[sq] = true;
        ++s.mort_count[owner];
    }
    s.p[0].cash = 110;
    s.p[1].cash = 5;
    s.p[0].cards = 1;
    s.p[1].cards = 2;
    condition_decks_on_held_cards(game);
    const auto chance = game.decks[0], chest = game.decks[1];
    assert(chance.size == 15 && chest.size == 15);
    s.rejected[0] = {1,-1,0,3};
    BundleResult deal;
    deal.give = bit(1) | bit(3) | bit(40);
    deal.take = bit(5) | bit(41);
    // Each brown deed requires $3. Neither can be acquired until both fees fit.
    assert(!settle_bundle_game(game,0,deal));
    assert(s.p[0].cash == 110 && s.p[1].cash == 5 && s.p[0].cards == 1);
    assert(s.owner[1] == 0 && s.owner[5] == 1 && s.rejected[0].turn == 3);
    s.p[1].cash = 36;
    assert(settle_bundle_game(game,0,deal));
    assert(s.owner[1] == 1 && s.owner[3] == 1 && s.owner[5] == 0);
    assert(s.count[0][0] == 0 && s.count[1][0] == 2 && s.count[0][Rail] == 1);
    assert(s.p[0].cash == 0 && s.p[1].cash == 0);
    assert(s.rejected[0].turn == 0 && s.mort_count[0] == 0 && s.mort_count[1] == 1);
    assert(!s.mort[1] && s.mort[3] && !s.mort[5]);
    assert((seen[0].deeds == std::vector<int>{5}) && seen[0].cash[0] == 110 && seen[0].price[0] == 110);
    assert((seen[1].deeds == std::vector<int>{1}) && seen[1].cash[0] == 33 && seen[1].price[0] == 33);
    assert(s.p[0].cards == 2 && s.p[1].cards == 1);
    assert(game.decks[0].ids == chance.ids && game.decks[0].head == chance.head && game.decks[0].size == 15);
    assert(game.decks[1].ids == chest.ids && game.decks[1].head == chest.head && game.decks[1].size == 15);
    assert(!settle_bundle_game(game,0,deal));
    game.return_card(1,0);
    game.return_card(0,1);
    assert(game.decks[0].size == 16 && game.decks[1].size == 16 && !s.p[0].cards && !s.p[1].cards);
    assert(!settle_bundle_game(game,0,BundleResult{}));
}
