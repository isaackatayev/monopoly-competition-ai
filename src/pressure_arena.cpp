// Reuse the published opponent definitions from the previous acquisition stage.
#define robust_batch robust_reference_batch
#include "robust_arena.cpp"
#undef robust_batch
#include "pressure_policy.hpp"
#include "deal_value.hpp"
#include "bundle_match.hpp"

namespace {
DealNet deal_net;
RobustWeights reference_prices;
std::array<double,6> third_historical_rival;
struct Entrants {
    Specialist specialist;
    ResilienceWeights resilience;
    PressureWeights prices;
    Policy neural,incumbent,entrant;
    Entrants(const double* candidate,const double* prior,const double* spec,const double* resilient) {
        std::copy_n(spec,SpecialistParams,specialist.w.begin());
        std::copy_n(resilient,ResilienceParams,resilience.w.begin());
        std::copy_n(candidate,PressureParams,prices.w.begin());
        neural=preset(6);
        std::copy_n(prior,Params,neural.w.begin());
        neural.net=&net;neural.response=&response;
        incumbent=neural;incumbent.style=9;incumbent.specialist=&specialist;
        incumbent.opponent=&oppnet;incumbent.resilience=&resilience;
        incumbent.robust=&reference_prices;incumbent.deal_net=&deal_net;
        entrant=incumbent;
        if(candidate[0]>=0) {
            entrant.style=candidate[9]>.5?11:10;
            entrant.pressure=&prices;
        }
    }
};
}
extern "C" int pressure_extras(const char* deal,const double* robust,const double* third) {
    std::copy_n(robust,RobustParams,reference_prices.w.begin());
    std::copy_n(third,6,third_historical_rival.begin());
    return load_deal_net(deal,deal_net);
}
extern "C" void pressure_batch(const double* candidate,const double* prior,const double* specialist,
        const double* resilience,const double* first_rival,const double* second_rival,
        int family,int /*unused*/,uint64_t seed,int games,int* output) {
    Entrants players(candidate,prior,specialist,resilience);
    Rng seeds{seed};std::fill_n(output,4,0);
    for(int i=0;i<games;i+=2) {
        const uint64_t board_seed=seeds.next();
        Rival market{players.neural,{},family};
        std::copy_n(family==9?second_rival:first_rival,6,market.bid.begin());
        auto draw=[&](double lo,double hi){return lo+(hi-lo)*seeds.below(10000)/10000.;};
        if(family==11)market.bid=third_historical_rival;
        if(family==12)
            market.bid={draw(.82,1.),draw(.8,1.3),draw(.45,.95),draw(0,.12),draw(.67,1.),draw(.55,1.)};
        if(family==13)
            market.bid={draw(.45,.95),draw(.55,1.35),draw(.35,1.15),draw(0,.45),draw(0,1.),draw(0,1.)};
        if(family==1 || family==2 || family==5 || family==8 || family==10) {
            market.bid={draw(.38,1.),draw(.50,1.),draw(.2,.97),draw(0,.70),draw(0,1.),draw(0,1.)};
            if(family==2){market.bid[0]=draw(.75,1.);market.bid[1]=draw(.65,.95);market.bid[4]=draw(.68,1.);}
        }
        Policy enemy=players.neural;
        if(family==3)enemy.style=4;
        else if(family==4)enemy.style=5;
        else if(family==7)enemy=preset(3);
        else if(family!=6){enemy.external=competing;enemy.context=&market;}
        for(int seat=0;seat<2;++seat){
            Game match(board_seed,seat?enemy:players.entrant,seat?players.entrant:enemy);
            int winner=match.play(1000);
            ++output[winner<0?2:int(winner!=seat)];output[3]+=match.s.turn;
        }
    }
}
extern "C" void pressure_bundle(const double* candidate,const double* prior,const double* specialist,
             const double* resilience,int fixture,uint64_t seed,int games,int* output) {
    Entrants players(candidate,prior,specialist,resilience);
    Policy enemy=fixture==0?players.incumbent:fixture==1?players.neural:preset(fixture==2?4:3);
    BundleStats stats{};
    bundle_matches(players.entrant,enemy,seed,games,1000,stats);
    output[0]=stats.wins;output[1]=stats.losses;output[2]=stats.caps;
    output[3]=stats.package_offers;output[4]=stats.package_accepted;
}
