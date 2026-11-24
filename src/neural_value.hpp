#pragma once
#include "game.hpp"
#include <array>

namespace mono {
constexpr int GlobalFeatures = 4, SeatFeatures = 62;
constexpr int NeuralInputs = GlobalFeatures + 2 * SeatFeatures;
constexpr int Hidden1 = 48, Hidden2 = 24;
using NeuralFeatures = std::array<float, NeuralInputs>;
struct NeuralNet {
    std::array<float, NeuralInputs * Hidden1> w1{};
    std::array<float, Hidden1> b1{};
    std::array<float, Hidden1 * Hidden2> w2{};
    std::array<float, Hidden2> b2{}, w3{};
    float temperature = 1;
};
NeuralFeatures neural_features(const State&, int who);
bool load_neural(const char* path, NeuralNet& destination);
float neural_value(const NeuralNet&, const State&, int who);
} // namespace mono
