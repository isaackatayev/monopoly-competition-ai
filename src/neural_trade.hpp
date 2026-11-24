#pragma once
#include "game.hpp"

namespace mono {
struct TradeAssessment {
    int legal = 0;
    float proposer_before = 0, proposer_after = 0;
    float recipient_before = 0, recipient_after = 0;
    float recipient_deal_gain=0;
    bool recipient_uses_deal_model=false;
    float acceptance_estimate = 0, expected_gain = 0;
};
TradeAssessment assess_trade(const Policy&,const State&,int proposer,const Action& offer);
Action neural_trade(const Policy&,const State&,int who,const Query&);
}
