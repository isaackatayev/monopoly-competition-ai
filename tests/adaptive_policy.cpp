#include "adaptive_policy.hpp"
#include "neural_value.hpp"
#include <cassert>

using namespace mono;
void test_adaptive_policy() {
    Policy base=preset(0);
    Specialist specialist;
    specialist.w.fill(.5);
    specialist.w[18]=0; // Reach a conclusion after one unambiguous contradictory price.
    Policy adaptive=base;
    adaptive.style=7;
    adaptive.specialist=&specialist;

    State public_state;
    public_state.owner.fill(-1);
    public_state.owner[12]=1;
    ++public_state.count[1][Utility];
    public_state.p[1].cash=1350;
    public_state.turn=6;

    // In either history the opponent owns the same utility. In the second case
    // we saw them pay $150 at face for their first utility, beyond a benchmark
    // bidder's $120 ceiling. No private auction ceiling was revealed.
    State informative=public_state;
    informative.behavior[1].append({BoughtAtFace,12,150,5});
    bool used_evidence=false;
    for(int sq=0;sq<Squares;++sq) {
        if(board[sq].group<0 || sq==12) continue;
        Query bid{Kind::Bid,sq,10};
        Action cold=adaptive_decide(adaptive,public_state,0,bid);
        Action revised=adaptive_decide(adaptive,informative,0,bid);
        assert(cold.value>=0 && cold.value<=public_state.p[0].cash);
        assert(revised.value==decide(base,informative,0,bid).value);
        used_evidence|=cold.value!=revised.value;
    }
    assert(used_evidence);
    // Trigger the public regime gate by observing a legal trade proposal.
    // Until that evidence, style 8 must reproduce all style 7 public decisions.
    NeuralNet net{};
    ResilienceWeights extension{{.10,.83,.85,1.28,.98,30,.40,.20}};
    adaptive.net=&net;
    Policy resilient=adaptive;
    resilient.style=8;
    resilient.resilience=&extension;
    State offered=public_state;
    offered.behavior[1].append({ProposedTrade,12,80,5,12,-1,120});
    bool switched=false;
    for(int sq=0;sq<Squares;++sq) {
        if(board[sq].group<0 || sq==12) continue;
        Query bid{Kind::Bid,sq,10};
        assert(decide(resilient,public_state,0,bid).value==decide(adaptive,public_state,0,bid).value);
        auto after=decide(resilient,offered,0,bid);
        assert(after.value>=0 && after.value<=offered.p[0].cash);
        switched|=after.value!=decide(adaptive,offered,0,bid).value;
    }
    assert(switched);
    public_state.p[0].cash=9;
    assert(adaptive_decide(adaptive,public_state,0,{Kind::Buy,39,400}).value==0);
    assert(adaptive_decide(adaptive,public_state,0,{Kind::Bid,39,10}).value<=9);
}
