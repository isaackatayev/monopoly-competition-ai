#include "four/game.hpp"
#include "groups.hpp"

#include <cassert>

namespace {
using namespace mono;
struct Script {
    int ceiling=0, bids=0;
    bool buy=false, redeem=false;
    Action management{};
};

Action scripted(void* context, const State&, int who, const Query& query) {
    assert(who == 0);
    auto& s = *static_cast<Script*>(context);
    if (query.kind == Kind::Buy) return {s.buy};
    if (query.kind == Kind::Bid) {
        ++s.bids;
        return {s.ceiling};
    }
    if (query.kind == Kind::Manage && query.square >= 0)
        return s.redeem ? Action{Redeem, query.square} : Action{};
    if (query.kind == Kind::Manage) {
        const Action result = s.management;
        s.management = {};
        return result;
    }
    return {};
}

struct Table {
    std::array<Script, four::Seats> scripts{};
    four::Game game{123, {}};
    Table() {
        for (int i = 0; i < four::Seats; ++i) {
            game.policies[i].external = scripted;
            game.policies[i].context = &scripts[i];
        }
    }
    void color(int who, int group) {
        for (int k = 0; k < group_size[group]; ++k) game.transfer(group_squares[group][k], who);
    }
    void draw_release_card(int who, int deck_index) {
        auto& deck = game.decks[deck_index];
        const int release = deck_index == 0 ? 7 : 4;
        const auto where = std::find(deck.ids.begin(), deck.ids.end(), release);
        std::iter_swap(deck.ids.begin(), where);
        game.card(who, deck_index);
        assert(deck.size == 15 && (game.players[who].cards & (1u << deck_index)));
    }
};
}

void test_four_economy() {
    using namespace mono;
    {
        Table t;
        auto& g = t.game;
        t.scripts[3].buy = true;
        g.property(3, 1, 7);
        assert(g.owner[1] == 3 && g.players[3].cash == 1440);
        t.scripts[0].ceiling = 60;
        t.scripts[1].ceiling = 160;
        t.scripts[2].ceiling = 900;
        t.scripts[3].ceiling = 300;
        g.players[2].cash = 100; // Its advertised ceiling cannot be financed.
        g.property(0, 3, 7);
        assert(g.owner[3] == 3 && g.players[3].cash == 1279);
        for (const auto& s : t.scripts) assert(s.bids == 1);
        g.property(2, 3, 8);
        assert(g.players[3].cash == 1287 && g.players[2].cash == 92);
        g.transfer(5, 2);
        g.transfer(15, 2);
        g.transfer(25, 0); // Third-party railroad does not contribute to seat 2's rent.
        g.property(3, 5, 7, 1);
        assert(g.players[2].cash == 192 && g.players[3].cash == 1187);
    }
    {
        Table t;
        auto& g = t.game;
        t.color(0, 0);
        t.color(1, 1);
        t.color(2, 2);
        t.color(3, 3);
        g.houses = 1;
        for (int i = 0; i < four::Seats; ++i) {
            t.scripts[i].management = {Build, group_squares[i][0]};
            t.scripts[i].ceiling = 50 * (i+1);
        }
        g.manage(1);
        assert(g.level[16] == 1 && g.level[6] == 0 && g.houses == 0);
        assert(g.players[3].cash == 1349);
        for (const auto& s : t.scripts) assert(s.bids == 1);
    }
    {
        Table t;
        auto& g = t.game;
        t.color(2, 0);
        g.level[1] = g.level[3] = 1;
        g.houses = 30;
        g.players[2].cash = 0;
        assert(g.pay(2, 1, 60));
        assert(g.players[1].cash == 1560 && g.players[2].cash == 20);
        assert(g.houses == 32 && g.mort[1] && !g.mort[3] && g.living() == 4);
    }
    {
        Table t;
        auto& g = t.game;
        t.draw_release_card(0, 0);
        g.transfer(12, 0);
        g.transfer(5, 0);
        g.mort[12] = g.mort[5] = true;
        g.players[0].cash = 7;
        g.players[2].cash = 186;
        t.scripts[2].redeem = true;
        assert(!g.pay(0, 2, 250));
        assert(g.living() == 3 && !g.alive[0] && g.winner() == -1);
        assert(g.players[0].cards == 0 && g.players[0].cash == 0);
        assert(g.players[2].cards == 1 && g.decks[0].size == 15);
        assert(g.owner[5] == 2 && g.owner[12] == 2 && !g.mort[5] && !g.mort[12]);
        assert(g.players[2].cash == 0); // $193 includes both principals and $18 of interest.
    }
    {
        Table t;
        auto& g = t.game;
        t.draw_release_card(1, 1);
        g.transfer(1, 1);
        g.mort[1] = true;
        g.players[1].cash = 0;
        t.scripts[1].ceiling = 1000;
        t.scripts[2].ceiling = 75;
        t.scripts[3].ceiling = 140;
        assert(!g.pay(1, -1, 5));
        assert(g.owner[1] == 3 && !g.mort[1] && g.players[3].cash == 1424);
        assert(g.decks[1].size == 16 && g.players[1].cards == 0);
        assert(t.scripts[1].bids == 0 && t.scripts[0].bids == 1);
    }
    {
        Table t;
        auto& g = t.game;
        g.players[0].cash = g.players[1].cash = 0;
        g.transfer(5, 0);
        g.mort[5] = true;
        t.scripts[3].ceiling = 40;
        assert(!g.pay(0, 1, 100));
        // Seat 1 cannot finance the $10 interest, so both seats go bankrupt.
        assert(!g.alive[0] && !g.alive[1] && g.living() == 2);
        assert(g.owner[5] == 3 && !g.mort[5] && g.players[3].cash == 1490);
        assert(t.scripts[0].bids == 0 && t.scripts[1].bids == 0);
        g.transfer(1, 2);
        g.mort[1] = true;
        g.players[2].cash = g.players[3].cash = 0;
        assert(!g.pay(2, 3, 1000));
        assert(g.winner() == 3 && g.living() == 1 && g.owner[1] == 3);
    }
    {
        Table t;
        auto& g = t.game;
        g.transfer(12, 2);
        g.mort[12] = true;
        g.players[2].cash = 82;
        t.scripts[2].redeem = true;
        g.mortgage_election(2, 12);
        assert(g.players[2].cash == 74 && g.mort[12]); // Could only pay the $8 interest.
    }
}
