// Standalone development arena for full-match population selection. The known
// qualifier remains a sparring partner; independent fixture populations are
// generated per seat-swapped seed. Only public game state reaches a strategy.
#include "adaptive_policy.hpp"
#include "robust_policy.hpp"
#include "neural_value.hpp"
#include "opponent.hpp"
#include "response.hpp"
#include <algorithm>
#include <cmath>
using namespace mono;
namespace {
NeuralNet net;
OpponentNet oppnet;
ResponseNet response;
struct Rival{Policy neural; std::array<double,6> bid; int family;};
Action competing(void* raw,const State& s,int who,const Query& q) {
    const auto& r=*static_cast<Rival*>(raw);
    const auto& w=r.bid;
    if(q.kind==Kind::Offer && w[4]<.66) return {};
    if(q.kind==Kind::Accept && w[4]<.33) return {};
    if(q.kind==Kind::Offer || q.kind==Kind::Accept) return decide(r.neural,s,who,q);
    if(q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0)) {
        int sq=q.square,g=board[sq].group;
        auto which=Query{Kind::Bid,sq,10};
        int block=holdout(s,who,which).value;
        int n=decide(r.neural,s,who,which).value;
        double premium=.5+w[1];
        if(s.count[1-who][g]+1==group_size[g]) premium*=.5+w[2];
        int cap=std::max(0,s.p[who].cash-int(240*w[3]));
        double value=((1-w[0])*block+w[0]*n)*premium;
        if(r.family==5 || r.family==8) {
            // Unseen generator: field-specific additive premiums, reserve proportional to exposure.
            value=.60*n+.40*board[sq].price+35+42*s.count[who][g];
            if(g==Rail)value+=100;
            if(g<Rail && s.count[1-who][g]+1==group_size[g])value+=220+w[2]*220;
            if(g<Rail && s.count[who][g]+1==group_size[g])value+=370+w[1]*180;
            cap=std::max(0,s.p[who].cash-35-int(w[3]*s.p[who].cash*.22));
            if(r.family==8) {
                // Distinct hybrid with fixed cash premiums, risk budget and status-sensitive pricing.
                bool complete=g<Rail && s.count[who][g]+1==group_size[g];
                bool denies=g<Rail && s.count[1-who][g]+1==group_size[g];
                value=.42*block+.28*n+.30*board[sq].price+board[sq].rents[0]*(1.5+w[1]*4);
                value+=complete?235:denies?160:45;
                if(g==Rail)value+=40+75*s.count[who][g];
                cap=std::max(0,s.p[who].cash-int(30+w[3]*225));
            }
        }
        if(r.family==10) {
            // Withheld nonlinear bidder. It alters liquidity and marginal valuation
            // as the bank runs out of deeds and does not use either challenger recipe.
            int remaining=28;for(int k=0;k<Groups;++k)remaining-=s.count[0][k]+s.count[1][k];
            double urgency=std::clamp((15-remaining)/15.,0.,1.);
            value=std::sqrt(double(std::max(1,block))*std::max(1,n))*(.95+.45*w[1])+
                    65*urgency*(1+s.count[who][g]);
            if(g<Rail && s.count[1-who][g]+1==group_size[g])value+=board[sq].rents[3]*(.25+.4*w[2]);
            cap=std::max(0,s.p[who].cash-int((55+110*w[3])*(1.-.55*urgency)));
        }
        int bid=std::clamp(int(value),0,cap);
        if(q.kind==Kind::Bid)return {bid};
        if(s.p[1-who].cash+1<board[sq].price) return {};
        return {bid>=board[sq].price};
    }
    return w[5]>=.5?decide(r.neural,s,who,q):holdout(s,who,q);
}
struct Learner {Policy incumbent;RobustWeights recipe;int mode;};
Action trial(void* raw,const State& s,int who,const Query& q) {
    const auto& a=*static_cast<const Learner*>(raw);
    return robust_trial(a.incumbent,s,who,q,a.recipe,a.mode);
}
}
extern "C" {
int robust_load(const char* n,const char* o,const char* r) {
    return load_neural(n,net)&&load_opponent(o,oppnet)&&load_response(r,response);
}
void robust_batch(const double* config,const double* prior,const double* specialist,const double* resilience,
          const double* first_rival,const double* second_rival,int family,int mode,uint64_t seed,int games,int* output) {
    bool changed=config[0]>=0;
    ResilienceWeights weights;
    std::copy_n(resilience,ResilienceParams,weights.w.begin());
    Policy neural=preset(6);std::copy_n(prior,Params,neural.w.begin());
    neural.net=&net;neural.response=&response;
    Specialist spec;std::copy_n(specialist,SpecialistParams,spec.w.begin());
    Policy base=neural;base.style=8;base.specialist=&spec;base.opponent=&oppnet;base.resilience=&weights;
    Learner learner{base,{},mode};
    std::copy_n(config,RobustParams,learner.recipe.w.begin());
    Policy us=base;
    if(config[0]==-2)us.style=7;
    if(changed){us.external=trial;us.context=&learner;}
    std::fill_n(output,4,0);Rng seeds{seed};
    for(int i=0;i<games;i+=2){
        uint64_t gs=seeds.next();Rival model{neural,{},family};
        std::copy_n(family==9?second_rival:first_rival,6,model.bid.begin());
        auto draw=[&](double lo,double hi){return lo+(hi-lo)*seeds.below(10000)/10000.;};
        if(family==1||family==2||family==5||family==8||family==10){
            model.bid={draw(.38,1.),draw(.50,1.),draw(.2,.97),draw(0,.70),draw(0,1.),draw(0,1.)};
            if(family==2){model.bid[0]=draw(.75,1.);model.bid[1]=draw(.65,.95);model.bid[4]=draw(.68,1.);}
        }
        Policy enemy=neural;
        if(family==3)enemy.style=4;
        else if(family==4)enemy.style=5;
        else if(family==6)enemy=neural;
        else if(family==7)enemy=preset(3);
        else {enemy.external=competing;enemy.context=&model;}
        for(int seat=0;seat<2;++seat){Game g(gs,seat?enemy:us,seat?us:enemy);int win=g.play(1000);++output[win<0?2:int(win!=seat)];output[3]+=g.s.turn;}
    }
}
}
