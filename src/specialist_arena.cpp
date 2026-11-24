// Native evaluation kernel used only by specialist_train.py. All networks are
// loaded once, before the Python trainer launches independent evaluation threads.
#include "adaptive_policy.hpp"
#include "neural_value.hpp"
#include "response.hpp"
#include "opponent.hpp"
#include <algorithm>
#include <cstdint>

using namespace mono;
namespace {
NeuralNet value_net;
ResponseNet response_net;
OpponentNet behavior_net;
bool has_behavior=false;
struct Rival {int variant,markup,buffer;Policy baseline;};
Action altered(void* context,const State& s,int who,const Query& q) {
    const auto& r=*static_cast<Rival*>(context);
    if(q.kind==Kind::Offer || q.kind==Kind::Accept) return {};
    if(q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0)) {
        int sq=q.square,g=board[sq].group;
        int price=holdout(s,who,{Kind::Bid,sq,10}).value;
        if(r.variant==2) {
            price=board[sq].price+65;
            if(g<Rail && (s.count[who][g]+1==group_size[g] || s.count[1-who][g]+1==group_size[g]))
                price+=450+board[sq].rents[3]/2;
            if(g==Rail)price+=55*s.count[who][g];
            if(g==Utility)price=150;
        }
        price=std::clamp(price*r.markup/100,0,std::max(0,s.p[who].cash-r.buffer));
        return {q.kind==Kind::Buy?int(price>=board[sq].price):price};
    }
    return r.variant==1?holdout(s,who,q):decide(r.baseline,s,who,q);
}
struct Candidate {Policy agent,prior;int phase;};
Action choose(void* ctx,const State& s,int who,const Query& q) {
    auto& c=*static_cast<Candidate*>(ctx);
    if(c.phase==1 && q.kind!=Kind::Buy && !(q.kind==Kind::Bid && q.amount>=0) && q.kind!=Kind::Manage)
        return decide(c.prior,s,who,q);
    if(c.phase==1 && q.kind==Kind::Manage) {
        Policy fast=c.agent;fast.net=nullptr;return adaptive_decide(fast,s,who,q);
    }
    return adaptive_decide(c.agent,s,who,q);
}
}
extern "C" {
int specialist_load(const char* value,const char* response,const char* opponent) {
    has_behavior=opponent && opponent[0] && load_opponent(opponent,behavior_net);
    return load_neural(value,value_net) && load_response(response,response_net) &&
        (!opponent || !opponent[0] || has_behavior);
}
void specialist_batch(const double* weights,const double* base,int phase,int rival,uint64_t seed,
                      int games,int cap,int* counts) {
    Specialist model;
    std::copy_n(weights,SpecialistParams,model.w.begin());
    Policy prior=preset(0);
    std::copy_n(base,Params,prior.w.begin());
    Candidate candidate{prior,prior,phase};
    auto& p=candidate.agent;p.style=7;p.specialist=&model;
    if(phase>0) {p.net=&value_net;p.response=&response_net;}
    if(phase>=2 && has_behavior) p.opponent=&behavior_net;
    Policy ours=p;ours.context=&candidate;ours.external=choose;
    if(phase==3) {ours=prior;ours.style=6;ours.net=&value_net;ours.response=&response_net;}
    std::fill_n(counts,4,0);
    Rng seeds{seed};
    for(int i=0;i<games;i+=2) {
        auto gs=seeds.next();
        Rival r{rival,75+seeds.below(66),seeds.below(100),preset(2)};
        Policy enemy=prior;
        if(rival==0) enemy.style=4;
        else if(rival==1 || rival==2) {enemy.external=altered;enemy.context=&r;}
        else if(rival==3) enemy.style=phase>=2?5:0;
        else if(rival==7) {
            if(phase>=2) {enemy.style=6;enemy.net=&value_net;enemy.response=&response_net;}
        } else enemy=preset(rival-3);
        for(int seat=0;seat<2;++seat) {
            Game g(gs,seat?enemy:ours,seat?ours:enemy);
            int result=g.play(cap);
            ++counts[result<0?2:int(result!=seat)];counts[3]+=g.s.turn;
        }
    }
}
}
