#pragma once
#include "game.hpp"

namespace mono {
constexpr int ResponseInputs=36, ResponseHidden=24;
using OfferFeatures=std::array<float,ResponseInputs>;
struct ResponseNet {
    std::array<float,ResponseInputs*ResponseHidden> w{};
    std::array<float,ResponseHidden> bias{}, out{};
    float intercept{};
};
OfferFeatures offer_features(const State&,int proposer,const Action&);
bool load_response(const char*,ResponseNet&);
float response_likelihood(const Policy&,const State&,int proposer,const Action&);
}
