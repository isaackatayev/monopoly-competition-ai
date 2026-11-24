#include "game.hpp"
#include "neural.hpp"
#include "../evaluation/policy.hpp"
#include <cmath>

namespace {
using namespace mono;
using namespace mono::four;

// Results use the physical seats, starting with the player who opens the game.
struct Result {
    int winner, turns, survivors;
    int offers[Seats][Seats], accepted[Seats][Seats];
    int accepted_types[Seats][4];
};
}

extern "C" int multiplayer_play(const evaluation::EvalConfig* entrants, int seats, uint64_t seed,
                                int cap, int with_trading, const double* const* weights, int weight_count,
                                Result* out, size_t config_size, size_t result_size) {
    if (!entrants || !out || (seats != 3 && seats != Seats) || cap < 1 ||
        config_size != sizeof(evaluation::EvalConfig) || result_size != sizeof(Result) ||
        weight_count != (weights ? MultiplayerParams : 0))
        return 0;
    std::array<Policy, Seats> policies{};
    for (int seat = 0; seat < seats; ++seat) {
        if (!entrants[seat].weights) return 0;
        policies[seat] = evaluation::entrant(entrants[seat]);
    }
    four::Game game(seed, policies, seats);
    for (int seat = 0; weights && seat < seats; ++seat) {
        const double* row = weights[seat];
        if (!row) continue;
        bool active = false;
        for (int i = 0; i < MultiplayerParams; ++i) {
            if (!std::isfinite(row[i]) || row[i] < -4. || row[i] > 4.) return 0;
            active = active || row[i] != 0.;
        }
        if (active) game.multiplayer_weights[seat] = row;
    }
    game.trading=with_trading;
    out->winner=game.play(cap);
    out->turns=game.turn;
    out->survivors=game.living();
    for (int who=0;who<Seats;++who) {
        std::copy_n(game.accepted_types[who].begin(), 4, out->accepted_types[who]);
        for (int other=0;other<Seats;++other) {
            out->offers[who][other]=game.offers[who][other];
            out->accepted[who][other]=game.accepted[who][other];
        }
    }
    return 1;
}

extern "C" int four_play(const evaluation::EvalConfig* entrants, uint64_t seed, int cap,
                         int with_trading, Result* out, size_t config_size, size_t result_size) {
    return multiplayer_play(entrants, Seats, seed, cap, with_trading, nullptr, 0,
                            out, config_size, result_size);
}
