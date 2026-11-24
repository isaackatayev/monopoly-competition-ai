#pragma once
#include "game.hpp"

namespace mono {
// Bit i denotes the deed on US board square i. Bits 40/41 denote the Chance/Chest jail cards. Arrays below are seat-indexed.
struct BundleResult {
    uint64_t give = 0, take = 0;
    int cash = 0, accept = 0;
    int fee[2]{}, cash_after[2]{}, full_colors[2]{}, cards_after[2]{};
    float before = 0, after = 0, willingness = 0, gain = 0, deal_gain = 0;
    int scalar = 0, used_deal_model = 0;
};
bool assess_bundle(const Policy&, const State&, int proposer, uint64_t give, uint64_t take,
                   int cash, BundleResult&);
bool settle_bundle_game(Game&, int proposer, const BundleResult&);
bool propose_bundle(const Policy&, const State&, int proposer, BundleResult&);
}
