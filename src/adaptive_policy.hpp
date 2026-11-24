#pragma once
#include "game.hpp"

namespace mono {
constexpr int SpecialistParams=20;
constexpr int ResilienceParams=8;
// Two scenario probabilities, a network blending fraction, two bid multipliers,
// a cash-valued purchase margin, an equity fraction, and a downside discount.
struct ResilienceWeights { std::array<double,ResilienceParams> w{}; };
extern const std::array<const char*,ResilienceParams> resilience_names;
Action resilient_decide(const Policy&,const State&,int who,const Query&);
// Used by the training arena to reproduce its ungated exploration round.
Action resilience_trial(const Policy&,const State&,int who,const Query&,bool require_evidence);
struct Specialist { std::array<double,SpecialistParams> w{}; };
extern const std::array<const char*,SpecialistParams> specialist_names;
Action adaptive_decide(const Policy&,const State&,int who,const Query&);
}
