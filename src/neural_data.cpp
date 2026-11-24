// Training data executable only. Emits one public position and both complementary outcomes per game.
#include "neural_value.hpp"
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace mono;
namespace mono {
Action neural_trade(const Policy&, const State&, int, const Query&) {
    throw std::logic_error("Negotiation policy selected during value training");
}
Action pressure_decide(const Policy&, const State&, int, const Query&) {
    throw std::logic_error("Pressure policy unavailable in the value-only trainer");
}
Action robust_decide(const Policy&, const State&, int, const Query&, const RobustWeights&) {
    throw std::logic_error("League policy selected during value training");
}
Action resilient_decide(const Policy&, const State&, int, const Query&) {
    throw std::logic_error("Bidding policy selected during value training");
}
Action adaptive_decide(const Policy&, const State&, int, const Query&) {
    throw std::logic_error("Adaptive policy selected during value training");
}
// Data collection uses frozen policies and never consults the value network being trained.
Action neural_decide(const Policy&, const State&, int, const Query&) {
    throw std::logic_error("Neural policy selected during value training");
}
}
namespace {
struct Behavior {
    Policy base;
    bool veto_trades;
};
Action act(void* ptr, const State& s, int p, const Query& q) {
    const auto& b = *static_cast<Behavior*>(ptr);
    if (b.veto_trades && (q.kind == Kind::Offer || q.kind == Kind::Accept)) return {};
    return decide(b.base, s, p, q);
}
Behavior sample_policy(Rng& rng, const Policy& champion) {
    int kind = rng.below(12);
    Behavior b{kind < 3 ? preset(1 + kind) : kind == 3 ? preset(0) : champion,
               rng.below(5) == 0};
    if (kind >= 7) {
        for (double& weight : b.base.w) {
            double noise = (rng.below(20001) - 10000) / 10000.;
            weight = std::clamp(weight + 0.50 * noise, 0., 1.);
        }
    }
    return b;
}
}

int main(int argc, char** argv) {
    if (argc != 6 + Params) {
        std::cerr << "usage: neural_data output count first_index split_seed cap weights...\n";
        return 2;
    }
    const char* path = argv[1];
    const int games = std::stoi(argv[2]);
    const uint64_t first = std::stoull(argv[3]), split = std::stoull(argv[4]);
    const int cap = std::stoi(argv[5]);
    Policy champion = preset(0);
    for (int k = 0; k < Params; ++k) champion.w[k] = std::stod(argv[k+6]);
    std::ofstream out(path, std::ios::binary);
    if (!out) return 1;
    int64_t capped = 0, turns = 0, seat0wins = 0;
    for (int i = 0; i < games; ++i) {
        // SplitMix64 is a permutation. Disjoint indexed game streams are stable under sharding.
        Rng ids{split + first + uint64_t(i)};
        Rng strategy{ids.next()}, samples{ids.next()};
        Behavior b0 = sample_policy(strategy, champion), b1 = sample_policy(strategy, champion);
        if (strategy.below(2)) std::swap(b0, b1);
        Policy p0 = b0.base, p1 = b1.base;
        p0.external = p1.external = act;
        p0.context = &b0;
        p1.context = &b1;
        Game game(ids.next(), p0, p1);
        State observation = game.s;
        // Reservoir sampling selects a uniformly random pre-turn state without reading the outcome.
        do {
            if (samples.below(game.s.turn + 1) == 0) observation = game.s;
            game.turn();
        } while (game.s.winner < 0 && game.s.turn < cap);
        auto f = neural_features(observation, 0);
        float label = game.s.winner < 0 ? 0.5f : float(game.s.winner == 0);
        out.write(reinterpret_cast<const char*>(f.data()), sizeof(f));
        out.write(reinterpret_cast<const char*>(&label), sizeof(label));
        capped += game.s.winner < 0;
        seat0wins += game.s.winner == 0;
        turns += game.s.turn;
    }
    out.close();
    if (!out) return 1;
    std::cout << games << " " << capped << " " << seat0wins << " " << turns << "\n";
}
