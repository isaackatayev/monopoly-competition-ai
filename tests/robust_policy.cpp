#include "adaptive_policy.hpp"
#include "neural_value.hpp"
#include "pressure_policy.hpp"
#include "robust_policy.hpp"
#include <cassert>

using namespace mono;

void test_robust_policy() {
    NeuralNet net{};
    Specialist specialist;
    specialist.w.fill(.5);
    ResilienceWeights resilience{{.1,.83,.85,1.28,.98,30,.40,.2}};
    RobustWeights model;
    model.w.fill(.5);

    Policy baseline=preset(0);
    baseline.style=8;
    baseline.specialist=&specialist;
    baseline.resilience=&resilience;
    baseline.net=&net;
    Policy entrant=baseline;
    entrant.style=9;
    entrant.robust=&model;
    State starting;
    starting.owner.fill(-1);
    State changed=starting;
    changed.behavior[1].append({ProposedTrade,12,150,0,12,-1,120});
    State bundle=starting;
    bundle.behavior[1].append({ProposedBundle,-1,150,0});
    bool revised=false;
    for(int sq=0;sq<Squares;++sq) {
        if(board[sq].group<0)continue;
        const Query q{Kind::Bid,sq,10};
        assert(decide(entrant,starting,0,q).value==decide(baseline,starting,0,q).value);
        const auto bid=decide(entrant,changed,0,q);
        assert(bid.value>=0 && bid.value<=changed.p[0].cash);
        assert(bid.value==decide(entrant,bundle,0,q).value);
        revised|=bid.value!=decide(baseline,changed,0,q).value;
    }
    assert(revised);
    PressureWeights pressure{{1.55,1.15,0,0,0,1,0,1,1,1.25,.60,.45}};
    Policy direct=entrant;
    direct.style=10;
    direct.pressure=&pressure;
    Policy conditional=direct;
    conditional.style=11;
    bool distinct=false;
    for(int sq=0;sq<Squares;++sq) {
        if(board[sq].group<0)continue;
        Query auction{Kind::Bid,sq,10};
        assert(decide(direct,starting,0,auction).value==decide(entrant,starting,0,auction).value);
        // A legal bundle proves the opponent trades. With no clearing prices or
        // cash pressure observed, only the direct series profile switches now.
        const int alternative=decide(direct,bundle,0,auction).value;
        assert(decide(conditional,bundle,0,auction).value==decide(entrant,bundle,0,auction).value);
        assert(alternative>=0 && alternative<=bundle.p[0].cash);
        distinct|=alternative!=decide(entrant,bundle,0,auction).value;
    }
    assert(distinct);
    assert(decide(direct,bundle,0,{Kind::Bid,-1,10}).value==0);
    State broke=bundle;
    broke.p[0].cash=7;
    assert(decide(direct,broke,0,{Kind::Buy,39,400}).value==0);
    assert(decide(direct,broke,0,{Kind::Bid,39,10}).value<=7);
    changed.p[0].cash=8;
    assert(decide(entrant,changed,0,{Kind::Buy,39,400}).value==0);
    assert(decide(entrant,changed,0,{Kind::Bid,39,10}).value<=8);
    assert(decide(entrant,changed,0,{Kind::Bid,40,10}).value==0);

    // The table entrant protects recoverable deed value and contested sets.
    Policy rematch=conditional;
    rematch.style=12;
    State orange=starting;
    orange.owner[16]=orange.owner[19]=1;
    orange.count[1][3]=2;
    const int tennessee=18;
    assert(decide(rematch,orange,0,{Kind::Buy,tennessee,180}).value==1);
    assert(decide(rematch,orange,0,{Kind::Bid,tennessee,10}).value>=280);
    for(int cash : {9, 55, 70, 100, 200, 500}) {
        orange.p[0].cash=cash;
        int original=decide(conditional,orange,0,{Kind::Bid,37,10}).value;
        int adjusted=decide(rematch,orange,0,{Kind::Bid,37,10}).value;
        int floor=std::clamp(cash-50,0,board[37].mortgage);
        assert(adjusted==std::max(original,floor>=10?floor:0));
    }
    assert(decide(rematch,orange,0,{Kind::Bid,tennessee,-1}).value==
           decide(conditional,orange,0,{Kind::Bid,tennessee,-1}).value);

    // High learned cash reserves must not repeatedly redeem and then mortgage
    // the last collateral while unowned deeds are still available.
    model.w[8]=1;
    changed.p[0].cash=800;
    for(int sq:{12,28}) {
        changed.owner[sq]=0;
        changed.mort[sq]=true;
        ++changed.count[0][Utility];
        ++changed.mort_count[0];
    }
    assert(decide(entrant,changed,0,{Kind::Manage}).value==Pass);
}
