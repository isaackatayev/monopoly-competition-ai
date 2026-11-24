#pragma once
#include "../game.hpp"

namespace mono::evaluation {
// Mirror native.Config for this evaluation plugin. The driver checks the field
// order and size, and ordinary games must reproduce the native tournament exactly.
struct EvalConfig {
    int style;
    const double* weights;
    const NeuralNet* net;
    const ResponseNet* response;
    const OpponentNet* opponent;
    const Specialist* specialist;
    const ResilienceWeights* resilience;
    const RobustWeights* robust;
    const DealNet* deal_net;
    const PressureWeights* pressure;
};
inline Policy entrant(const EvalConfig& c) {
    auto p = preset(c.style);
    std::copy_n(c.weights, Params, p.w.begin());
    p.net = c.net;
    p.response = c.response;
    p.opponent = c.opponent;
    p.specialist = c.specialist;
    p.resilience = c.resilience;
    p.robust = c.robust;
    p.deal_net = c.deal_net;
    p.pressure = c.pressure;
    return p;
}

}
