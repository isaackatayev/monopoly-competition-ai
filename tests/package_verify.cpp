// Use the actual bundle-match negotiation window. Toggle only the incumbent's
// package decisions, and count offers and settlements by the seat that proposed.
#include "bundles/verified.hpp"
#include "adaptive_policy.hpp"
#include "robust_policy.hpp"
#include "neural_value.hpp"
#include "opponent.hpp"
#include "response.hpp"
#include "deal_value.hpp"
#include <iostream>

namespace mono {
namespace {
bool verify;
int proposed[2]{},accepted[2]{};
bool package(const BundleResult& deal) {
    constexpr uint64_t deeds=(uint64_t{1}<<Squares)-1;
    auto multiple=[](uint64_t mask){return bool(mask && (mask&(mask-1)));};
    return ((deal.give|deal.take)&~deeds) || multiple(deal.give&deeds) || multiple(deal.take&deeds);
}
bool measured_offer(const Policy& p,const State& s,int seat,BundleResult& out) {
    bool ok=verify && p.style==9 ? verified_bundle_offer(p,s,seat,out) : package_offer(p,s,seat,out);
    if(ok && package(out))++proposed[seat];
    return ok;
}
bool measured_assess(const Policy& p,const State& s,int seat,uint64_t give,uint64_t take,int cash,
                     BundleResult& out) {
    return verify && p.style==9 ? verified_bundle_assessment(p,s,seat,give,take,cash,out)
                                : package_assessment(p,s,seat,give,take,cash,out);
}
bool measured_settlement(Game& game,int seat,const BundleResult& deal) {
    bool ok=settle_bundle_game(game,seat,deal);
    if(ok && package(deal))++accepted[seat];
    return ok;
}
}
}
#define package_offer measured_offer
#define package_assessment measured_assess
#define settle_bundle_game measured_settlement
#include "../src/bundle_match.cpp"
#undef package_offer
#undef package_assessment
#undef settle_bundle_game

int main(int argc,char**argv) {
    using namespace mono;
    if(argc!=9)return 1;
    int fixture=std::stoi(argv[1]),first=std::stoi(argv[2]),count=std::stoi(argv[3]);
    if((fixture!=1 && fixture!=4 && fixture!=7) || first<0 || count<1)return 1;
    Rng rng{std::stoull(argv[4])+uint64_t(first)*0x9e3779b97f4a7c15ULL};
    NeuralNet value;ResponseNet response;OpponentNet opponent;DealNet deal;
    if(!load_neural(argv[5],value)||!load_response(argv[6],response)||!load_opponent(argv[7],opponent)
       ||!load_deal_net(argv[8],deal))return 1;
    Specialist specialist;ResilienceWeights resilience;RobustWeights robust;
    Policy entrant=preset(9);entrant.net=&value;entrant.response=&response;entrant.opponent=&opponent;
    entrant.specialist=&specialist;entrant.robust=&robust;entrant.resilience=&resilience;entrant.deal_net=&deal;
    for(auto& w:entrant.w)if(!(std::cin>>w))return 1;
    for(auto& w:specialist.w)if(!(std::cin>>w))return 1;
    for(auto& w:resilience.w)if(!(std::cin>>w))return 1;
    for(auto& w:robust.w)if(!(std::cin>>w))return 1;
    Policy rival=preset(fixture);
    if(fixture==7) {
        rival=entrant;rival.style=7;rival.resilience=nullptr;rival.robust=nullptr;rival.deal_net=nullptr;
    }
    for(int i=first;i<first+count;++i) {
        auto seed=rng.next();
        for(int seat=0;seat<2;++seat) for(int mode=0;mode<2;++mode) {
            proposed[0]=proposed[1]=accepted[0]=accepted[1]=0;verify=bool(mode);
            BundleStats stats;
            Game game(seed,seat?rival:entrant,seat?entrant:rival);
            game.trade_window=negotiate;game.trade_window_context=&stats;
            int winner=game.play(1000);
            std::cout<<i<<' '<<seat<<' '<<mode<<' '<<(winner<0?2:int(winner!=seat))<<' '<<game.s.turn;
            for(int p:{seat,1-seat})
                std::cout<<' '<<game.offers_made[p]-proposed[p]<<' '<<game.offers_closed[p]-accepted[p]
                         <<' '<<proposed[p]<<' '<<accepted[p];
            std::cout<<' '<<stats.multi_deed_accepted<<' '<<stats.card_accepted<<'\n';
        }
    }
}
