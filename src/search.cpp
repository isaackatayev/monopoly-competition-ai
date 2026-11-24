#include "game.hpp"

#include <cmath>

namespace mono {
namespace {
constexpr int trials = 60;
constexpr int horizon = 140;
constexpr int cap = 1000;

uint64_t public_seed(const State& s, const Query& trade) {
    uint64_t hash = 780139124033ULL;
    auto add = [&](int value) {
        hash ^= static_cast<uint32_t>(value);
        hash *= 1099511628211ULL;
    };
    add(s.turn);
    add(trade.give);
    add(trade.take);
    add(trade.cash);
    for (const auto& player : s.p) {
        add(player.cash);
        add(player.pos);
        add(player.jail);
        add(player.cards);
    }
    for (int square = 0; square < Squares; ++square) {
        add(s.owner[square]);
        add(s.level[square]);
        add(s.mort[square]);
    }
    return hash;
}

} // namespace

void condition_decks_on_held_cards(Game& game) {
    // A newly constructed deck is a random permutation with head at zero.
    // Removing a known held card leaves a uniform permutation of its fifteen peers.
    constexpr int release_id[] = {7, 4};
    unsigned held = game.s.p[0].cards | game.s.p[1].cards;
    for (int d = 0; d < 2; ++d) {
        if ((held & (1u << d)) == 0) continue;
        auto& deck = game.decks[d];
        auto card = std::find(deck.ids.begin(), deck.ids.end(), release_id[d]);
        std::iter_swap(card, deck.ids.end() - 1);
        --deck.size;
    }
}

namespace {
struct PendingTrade {
    const Policy& base;
    const Query& trade;
    Kind override_kind;
    bool accept;
    bool used = false;
};

Action first_trade(void* context, const State& s, int who, const Query& q) {
    auto& pending = *static_cast<PendingTrade*>(context);
    if (!pending.used && q.kind == pending.override_kind) {
        pending.used = true;
        if (q.kind == Kind::Accept) return {pending.accept};
        return {1, -1, pending.trade.give, pending.trade.take, pending.trade.cash};
    }
    return decide(pending.base, s, who, q);
}

double continuation(uint64_t seed, const State& s, int who, const Query& trade,
                    const Policy& base, const Policy& opponent, bool accept) {
    PendingTrade response{base, trade, Kind::Accept, accept};
    PendingTrade offer{opponent, trade, Kind::Offer, false};
    Policy responder = base, proposer = opponent;
    responder.external = first_trade;
    responder.context = &response;
    proposer.external = first_trade;
    proposer.context = &offer;
    Game game(seed, who == 0 ? responder : proposer, who == 0 ? proposer : responder);
    game.s = s;
    game.s.active = 1 - who;
    // The real request arrived inside trade(), after turn() incremented the counter.
    // Re-enter that window with identical starting random streams in both branches.
    game.s.turn = std::max(0, s.turn - 1);
    condition_decks_on_held_cards(game);
    game.turn();
    game.policies[who] = &base;
    game.policies[1 - who] = &opponent;
    int winner = game.play(std::min(cap, s.turn + horizon));
    return winner == -1 ? 0.5 : double(winner == who);
}
} // namespace

Action search_accept(const Policy& learned, const State& s, int who, const Query& trade) {
    Policy base = learned;
    base.style = 0;
    base.external = nullptr;
    base.context = nullptr;
    const Action fallback = decide(base, s, who, trade);
    if (trade.kind != Kind::Accept || s.turn >= cap || (who != 0 && who != 1)) return fallback;
    Rng rng{public_seed(s, trade)};
    const Policy opponents[] = {preset(3), preset(1), preset(2), base};
    double advantage = 0;
    for (int i = 0; i < trials; ++i) {
        const uint64_t seed = rng.next();
        const auto& opponent = opponents[i % 4];
        const double reject = continuation(seed, s, who, trade, base, opponent, false);
        const double accept = continuation(seed, s, who, trade, base, opponent, true);
        advantage += accept - reject;
    }
    // Keep the trained action unless the simulations favor an alternative by over 5 points.
    if (std::abs(advantage) <= trials * 0.05) return fallback;
    return {advantage > 0};
}
} // namespace mono
