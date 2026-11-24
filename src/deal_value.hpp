#pragma once
#include "neural_value.hpp"
#include "response.hpp"

namespace mono {
constexpr int DealInputs=86, DealHidden=32;
using DealFeatures=std::array<float,DealInputs>;
struct DealNet {
    std::array<float,DealInputs*DealHidden> w{};
    std::array<float,DealHidden> bias{}, out{};
    float intercept=0;
};
DealFeatures deal_features(const Policy&,const State&,const State& after,int recipient,const Action&);
bool load_deal_net(const char* path,DealNet&);
float recipient_gain(const DealNet&,const DealFeatures&);
}
