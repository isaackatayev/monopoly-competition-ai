// Evaluation plugin only. Link to the frozen simulator using rule_shift_bench.py.
#include "evaluation/policy.hpp"
#include <cstdint>

namespace {
using namespace mono;

using mono::evaluation::EvalConfig;
using mono::evaluation::entrant;

int64_t net_worth(const State& s,int seat) {
    int64_t wealth = s.p[seat].cash;
    for (int sq = 0; sq < Squares; ++sq) {
        if (s.owner[sq] != seat) continue;
        const auto& d = board[sq];
        wealth += d.price - (s.mort[sq] ? d.mortgage : 0);
        // A hotel represents five paid building levels on its deed.
        wealth += s.level[sq] * (d.house / 2);
    }
    return wealth;
}

struct Counts {
    int64_t bankruptcy_wins = 0, bankruptcy_losses = 0, cap_draws = 0;
    int64_t net_worth_wins = 0, net_worth_losses = 0, net_worth_ties = 0;
    int64_t turns = 0, reached_shift = 0, offers = 0, deals = 0;
    int64_t offers_from_turn_40 = 0, deals_from_turn_40 = 0;
};
int total(const int counts[2]) { return counts[0] + counts[1]; }

void record(const Game& game,int seat,bool adjudicate,int early_offers,int early_deals,Counts& c) {
    const auto& s = game.s;
    if (s.winner >= 0) {
        if (s.winner == seat) ++c.bankruptcy_wins;
        else ++c.bankruptcy_losses;
    } else if (adjudicate) {
        const auto difference = net_worth(s,seat) - net_worth(s,1-seat);
        c.net_worth_wins += difference > 0;
        c.net_worth_losses += difference < 0;
        c.net_worth_ties += difference == 0;
    } else {
        ++c.cap_draws;
    }
    c.turns += s.turn;
    c.reached_shift += s.turn >= 40;
    const int offers = total(game.offers_made), deals = total(game.offers_closed);
    c.offers += offers;
    c.deals += deals;
    c.offers_from_turn_40 += offers - early_offers;
    c.deals_from_turn_40 += deals - early_deals;
}
}

extern "C" int mono_rule_shift_layout(size_t config_size,size_t counts_size) {
    // Independent worked example. Cash $300, both brown deeds $120, four houses
    // and a hotel at half building cost $225, and a mortgaged railroad worth $100.
    mono::State s;
    s.owner.fill(-1);
    s.p[0].cash = 300;
    s.p[0].cards = 3;
    for (int sq : {1,3,5}) s.owner[sq] = 0;
    s.level[1] = 4;
    s.level[3] = 5;
    s.mort[5] = true;
    return config_size == sizeof(EvalConfig) && counts_size == sizeof(Counts) && net_worth(s,0) == 745;
}

// One common initial seed, both seats, in a two-by-two rule design.
// Rows 0/1 allow ordinary trades and rows 2/3 ban trades from turn 40.
// Even rows use bankruptcy at 1000 turns and odd rows adjudicate at 100.
extern "C" void mono_rule_shift_pair(EvalConfig a,EvalConfig b,uint64_t seed,Counts* rows) {
    const auto player = entrant(a), rival = entrant(b);
    for (int i=0; i<4; ++i) rows[i] = {};
    for (int seat = 0; seat < 2; ++seat) {
        mono::Game original(seed,seat ? rival : player,seat ? player : rival);
        original.play(39);
        const int early_offers = total(original.offers_made), early_deals = total(original.offers_closed);
        for (int ban = 0; ban <= 1; ++ban) {
            // Copy the full board, RNG and both decks at the boundary. Baseline
            // and altered games are therefore identical through player-turn 39.
            auto game = original;
            if (ban) game.trade_window = [](mono::Game&,int) {};
            game.play(100);
            record(game,seat,true,early_offers,early_deals,rows[2*ban+1]);
            game.play(1000);
            record(game,seat,false,early_offers,early_deals,rows[2*ban]);
        }
    }
}
