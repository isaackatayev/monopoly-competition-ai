#include "verified.hpp"
#include "../neural_value.hpp"

namespace mono {
namespace {
struct Negotiation {
    int proposed=0, proposed_packages=0, declined_packages=0, accepted_packages=0;
    bool active() const {return proposed>0 || declined_packages>=2;}
    bool selective() const {
        return declined_packages>=2 && declined_packages>2*accepted_packages
               && (proposed_packages>0 || declined_packages>=4);
    }
};
Negotiation observed(const BehaviorHistory& history) {
    Negotiation n;
    for(int i=0;i<history.size;++i) {
        auto kind=history.events[i].kind;
        n.proposed+=kind==ProposedTrade || kind==ProposedBundle;
        n.proposed_packages+=kind==ProposedBundle;
        n.declined_packages+=kind==DeclinedBundle;
        n.accepted_packages+=kind==AcceptedBundle;
    }
    return n;
}
double future_gain(const Policy& model,const State& s,int proposer,const BundleResult& deal,
                   int seat,int trials,int horizon) {
    // Retain received mortgages for this projection, as in the package scorer.
    auto retain=preset(0);
    retain.external=[](void*,const State&,int,const Query&){return Action{};};
    Game projected(0,retain,retain);
    projected.s=s;
    if(!settle_bundle_game(projected,proposer,deal))return 0;
    Policy rollout=preset(0);
    rollout.w=model.w;
    Rng seeds{uint64_t(s.turn)*0x369dea0f31a53f85ULL+uint64_t(s.p[0].pos*Squares+s.p[1].pos)};
    auto future=[&](const State& position,uint64_t seed) {
        Game simulation(seed,rollout,rollout);
        simulation.s=position;
        simulation.s.turn=std::max(0,position.turn-1);
        simulation.trade_window=[](Game&,int){};
        condition_decks_on_held_cards(simulation);
        simulation.play(simulation.s.turn+horizon);
        return neural_value(*model.net,simulation.s,seat);
    };
    double delta=0;
    for(int i=0;i<trials;++i) {
        auto seed=seeds.next();
        delta+=future(projected.s,seed)-future(s,seed);
    }
    return delta/trials;
}
BundleResult scalar_offer(const Policy& p,const State& s,int who) {
    BundleResult out;
    const auto offer=decide(p,s,who,{Kind::Offer});
    if(legal_trade(s,who,offer)) {
        const uint64_t give=offer.give<0?0:uint64_t{1}<<offer.give;
        const uint64_t take=offer.take<0?0:uint64_t{1}<<offer.take;
        assess_bundle(p,s,who,give,take,offer.cash,out);
    }
    return out;
}
}
bool verified_bundle_offer(const Policy& p,const State& s,int who,BundleResult& out) {
    if(!p.net || (who!=0 && who!=1))return false;
    const auto rival=observed(s.behavior[1-who]);
    // Probe for another rich deal every sixth own turn after a string of refusals.
    if(rival.selective() && (s.turn/2)%6!=0) {
        out=scalar_offer(p,s,who);
        return true;
    }
    if(!propose_bundle(p,s,who,out))return false;
    if(out.scalar)return true;
    const auto fallback=scalar_offer(p,s,who);
    if(!(out.give || out.take)) {out=fallback;return true;}
    const double continuation=future_gain(p,s,who,out,who,6,36);
    const double gain=.18*(out.after-out.before)+.82*continuation;
    if(gain<(rival.active()?.018:.004) || gain*out.willingness<fallback.gain+.0008)
        out=fallback;
    return true;
}

bool verified_bundle_assessment(const Policy& p,const State& s,int proposer,uint64_t give,uint64_t take,
                                int cash,BundleResult& result) {
    if(!assess_bundle(p,s,proposer,give,take,cash,result))return false;
    if(result.scalar || !(give || take) || !result.accept)return true;
    const double delta=future_gain(p,s,proposer,result,1-proposer,10,60);
    const double gain=.34*(result.before-result.after)+.66*delta;
    result.accept=delta>-.012 && gain>(observed(s.behavior[proposer]).active()?.013:.003);
    return true;
}
}
