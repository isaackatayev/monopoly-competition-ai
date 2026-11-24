#pragma once
#include "game.hpp"
namespace mono {
constexpr int BehaviorInputs = 16, BehaviorHidden = 12, BehaviorGates = 3 * BehaviorHidden;
using EventFeatures = std::array<float, BehaviorInputs>;
struct OpponentNet {
    std::array<float, BehaviorInputs * BehaviorGates> input{};
    std::array<float, BehaviorHidden * BehaviorGates> recurrent{};
    std::array<float, BehaviorGates> bias{};
    std::array<float, BehaviorHidden * 2> output{};
    std::array<float, 2> intercept{};
};
int offer_generosity(const State&, int proposer, const Action&);
EventFeatures opponent_features(const BehaviorEvent&, int previous_turn);
bool load_opponent(const char*, OpponentNet&);
std::array<float,2> opponent_beliefs(const Policy&, const State&, int opponent);
float refusal_probability(const Policy&, const State&, int opponent);
float opponent_aggressiveness(const Policy&, const State&, int opponent);
} // namespace mono
