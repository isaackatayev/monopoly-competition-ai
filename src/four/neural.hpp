#pragma once
#include "game.hpp"

namespace mono::four {
inline constexpr int MultiplayerParams = 24;

// A null or all-zero readout preserves the frozen duel policy exactly.
bool multiplayer_enabled(const double* weights);
bool multiplayer_trades_enabled(const double* weights);
Action multiplayer_action(const Game&, int who, int rival, const State&, const Query&, Action base);
struct OfferChoice {
    Action offer;
    double score;
};
OfferChoice multiplayer_offer(const Game&, int who, int rival, const State&, Action base);
}
