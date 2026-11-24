#include "game.hpp"
#include <cassert>
using namespace mono;

namespace {
State empty_state() {
    State s{};
    s.owner.fill(-1);
    return s;
}
void assign(State& s,int p,int sq) {
    s.owner[sq]=p;
    ++s.count[p][board[sq].group];
}
}
void test_policy() {
    const Policy p=preset(0);
    auto s=empty_state();
    int first=decide(p,s,0,{Kind::Bid,19,10}).value;
    assign(s,1,16);
    assign(s,1,18);
    int block=decide(p,s,0,{Kind::Bid,19,10}).value;
    assert(block>first && block<=s.p[0].cash);
    s.p[1].cash=20;
    assert(decide(p,s,0,{Kind::Buy,19,200}).value==0);
    assert(decide(p,s,0,{Kind::Bid,19,10}).value>20);
    assign(s,0,19);
    // The opponent would complete orange for $1.
    assert(decide(p,s,0,{Kind::Accept,-1,0,-1,19,1}).value==0);

    s=empty_state();
    assign(s,0,16);assign(s,0,18);assign(s,0,19);
    auto build=decide(p,s,0,{Kind::Manage});
    assert(build.value==Build && can_build(s,0,build.square));
    assign(s,0,39);
    auto loan=decide(p,s,0,{Kind::Liquidate,-1,2000});
    assert(loan.value==Mortgage && loan.square==39);
    s.level[16]=s.level[18]=s.level[19]=5;
    s.p[1].jail=0;
    assert(decide(p,s,1,{Kind::Jail}).value==0);
    s=empty_state();
    s.p[0].jail=0;s.p[0].cards=2;
    assert(decide(p,s,0,{Kind::Jail}).value==3);

    // The recipient buys the last orange deed at a modest cash price.
    s=empty_state();
    assign(s,0,16);assign(s,0,18);assign(s,1,19);
    assert(decide(p,s,0,{Kind::Accept,-1,0,19,-1,-150}).value==1);
    // A scarce hotel conversion on light blue gives the rival enough houses
    // to develop orange. Keeping four houses on each deed blocks that build.
    s=empty_state();
    for(int q:{6,8,9}) {assign(s,0,q);s.level[q]=4;}
    for(int q:{16,18,19}) {assign(s,1,q);s.level[q]=1;}
    s.houses=0;
    assert(decide(p,s,0,{Kind::Manage}).value==Pass);
    s.houses=10;
    assert(decide(p,s,0,{Kind::Manage}).value==Build);
    // The advisory endpoint also rejects an offer for a railroad the proposer lacks.
    assert(decide(p,s,0,{Kind::Accept,-1,0,5,-1,-50}).value==0);
    for(int style=0;style<4;++style) {
        for(double w:preset(style).w) assert(w>=0 && w<=1);
    }
}
