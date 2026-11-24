#include "neural_trade.hpp"
#include "neural_value.hpp"
#include "response.hpp"

#include <cassert>
#include <cmath>

using namespace mono;
namespace {
void own(State& s,int seat,int sq) {
    s.owner[sq]=seat;
    ++s.count[seat][board[sq].group];
}
NeuralNet single_feature(int feature,float weight) {
    NeuralNet net{};
    net.w1[feature*Hidden1]=1;
    net.w2[0]=1;
    net.w3[0]=weight;
    return net;
}
}

void test_neural_trade() {
    State s;
    s.owner.fill(-1);
    s.turn=12; s.active=0;
    own(s,0,6); own(s,0,8); own(s,1,9);
    // Isolate a learned feature in a deterministic network to establish that
    // changing only the weights can reverse an actual accept/decline decision.
    auto values_color=single_feature(GlobalFeatures+5+1,4.f);
    auto values_cash=single_feature(GlobalFeatures+Groups*5,6.f);
    auto flat=NeuralNet{};
    Policy pol=preset(0);
    pol.net=&values_color;
    const Action offer{1,-1,9,-1,-250}; // seat 1 sells Connecticut to seat 0
    auto a=assess_trade(pol,s,1,offer);
    assert(a.legal && a.recipient_after>a.recipient_before+.45f);
    assert(a.proposer_after<a.proposer_before);
    assert(a.acceptance_estimate>=0 && a.acceptance_estimate<=1);
    assert(std::abs(a.expected_gain-a.acceptance_estimate*(a.proposer_after-a.proposer_before))<1e-6);
    Query proposed{Kind::Accept,-1,0,offer.give,offer.take,offer.cash};
    assert(neural_trade(pol,s,0,proposed).value==1);
    pol.net=&values_cash;
    assert(neural_trade(pol,s,0,proposed).value==0);
    pol.net=&flat;
    assert(neural_trade(pol,s,0,proposed).value==0);
    assert(neural_trade(pol,s,0,{Kind::Offer}).value==0);
    pol.net=&values_color;
    auto outgoing=neural_trade(pol,s,0,{Kind::Offer});
    assert(outgoing.value==1 && outgoing.take==9 && legal_trade(s,0,outgoing));
    auto outbound=assess_trade(pol,s,0,outgoing);
    assert(outbound.expected_gain>0);
    s.rejected[0]={outgoing.give,outgoing.take,outgoing.cash,s.turn-2};
    auto revised=neural_trade(pol,s,0,{Kind::Offer});
    assert(!revised.value || revised.give!=outgoing.give || revised.take!=outgoing.take ||
           revised.cash>outgoing.cash);
    s.rejected[0]={};
    ResponseNet unwilling{};
    unwilling.intercept=-15.f;
    pol.response=&unwilling;
    assert(assess_trade(pol,s,0,outgoing).acceptance_estimate<1e-5f);
    assert(neural_trade(pol,s,0,{Kind::Offer}).value==0);
    pol.response=nullptr;

    // A house anywhere in the outgoing color forbids its transfer; cash
    // required for mortgage interest must also be available at settlement.
    assert(!assess_trade(pol,s,0,offer).legal);
    assert(!assess_trade(pol,s,0,{1,-1,-1,-1,10}).legal);
    own(s,0,1); own(s,0,3);
    s.level[3]=1;
    assert(!assess_trade(pol,s,0,{1,-1,1,9,10}).legal);
    s.mort[9]=true; s.mort_count[1]=1; s.p[0].cash=250;
    assert(!assess_trade(pol,s,1,offer).legal);
    s.p[0].cash=256;
    assert(assess_trade(pol,s,1,offer).legal);
    assert(s.mort[9] && s.p[0].cash==256 && s.owner[9]==1);
}
