#include "game.hpp"
#include "neural_value.hpp"
#include <cassert>

namespace {
using namespace mono;
State blank() {
    State s;
    s.owner.fill(-1);
    return s;
}
NeuralNet single_feature(int feature,float slope) {
    NeuralNet n;
    n.w1[feature*Hidden1]=1;
    n.w2[0]=1;
    n.w3[0]=slope;
    return n;
}
void own(State& s,int p,int sq) {
    s.owner[sq]=p;
    ++s.count[p][board[sq].group];
}
}

void test_neural_policy() {
    using namespace mono;
    Policy p=preset(0);
    p.style=6;
    State s=blank();
    // Identical board and prior, opposite learned opinions of rail holdings.
    const int rail_feature=GlobalFeatures+5*Rail;
    auto favor=single_feature(rail_feature,8),avoid=single_feature(rail_feature,-8);
    p.net=&favor;
    int high=neural_decide(p,s,0,{Kind::Bid,5,10}).value;
    p.net=&avoid;
    int low=neural_decide(p,s,0,{Kind::Bid,5,10}).value;
    assert(high>low+50 && high<=s.p[0].cash && low>=0);

    // An artificial network that assigns a very high value to orange development
    // invests, while an otherwise identical network with the opposite sign waits.
    for(int sq:{16,18,19}) own(s,0,sq);
    auto build=single_feature(GlobalFeatures+5*3+3,50);
    auto wait=single_feature(GlobalFeatures+5*3+3,-50);
    p.net=&build;
    auto a=neural_decide(p,s,0,{Kind::Manage});
    assert(a.value==Build && can_build(s,0,a.square));
    p.net=&wait;
    a=neural_decide(p,s,0,{Kind::Manage});
    assert(a.value==Pass);

    // The legal liquidation window always produces an action that raises cash.
    s.p[0].cash=5;
    a=neural_decide(p,s,0,{Kind::Liquidate,-1,100});
    assert(a.value==Mortgage && can_mortgage(s,0,a.square));
    s.p[0].jail=0;
    auto jail=neural_decide(p,s,0,{Kind::Jail});
    assert(jail.value==0);
}
