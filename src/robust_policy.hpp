#pragma once
#include "game.hpp"

namespace mono {
constexpr int RobustParams=12;
constexpr int RobustEvidenceMode=4;
struct RobustWeights {std::array<double,RobustParams> w{};};
extern const std::array<const char*,RobustParams> robust_names;
Action robust_trial(const Policy&,const State&,int who,const Query&,const RobustWeights&,int mode);
Action robust_decide(const Policy&,const State&,int who,const Query&,const RobustWeights&);
}
