#pragma once
#include "game.hpp"

namespace mono {
constexpr int PressureParams=12;
struct PressureWeights {std::array<double,PressureParams> w{};};
extern const std::array<const char*,PressureParams> pressure_names;
Action pressure_decide(const Policy&,const State&,int,const Query&);
}
