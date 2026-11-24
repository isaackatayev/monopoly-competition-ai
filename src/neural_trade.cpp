#include "neural_trade.hpp"
#include "neural_value.hpp"
#include "response.hpp"
#include "deal_value.hpp"
#include "opponent.hpp"

#include <cmath>
#include <limits>

namespace mono {
namespace {
Policy base_of(const Policy& pol) {
    Policy base = pol;
    base.style = 0;
    base.net = nullptr;
    base.deal_net = nullptr;
    base.external = nullptr;
    base.context = nullptr;
    return base;
}

struct Terms {
    int square = -1;
    double proposer = 0, recipient = 0;
};

Terms deed_terms(const Policy& base, const State& s, int p, int square, bool taking) {
    if (square == -1) return {};
    return {square, trade_deed_value(base,s,p,square,taking),
                    trade_deed_value(base,s,1-p,square,!taking)};
}

// Opponent preferences are unknown. Estimate willingness under three public
// utility models: asset growth, liquidity, and relative position. This is a
// structural estimate of acceptance, distinct from the network's win estimate.
double soft_vote(double value, double scale) {
    return .5 + .5 * value / (std::abs(value) + scale);
}
double cash_gain(int initial, int change) {
    const auto utility = [](double x) {
        return .75*x + .40*std::min(850.,x) + .60*std::min(300.,x);
    };
    return utility(initial + double(change)) - utility(initial);
}
struct Market { double mine, acceptance; };
Market market(const State& s,int p,const Terms& give,const Terms& take,int cash) {
    double my_deeds = give.proposer + take.proposer;
    double their_deeds = give.recipient + take.recipient;
    if (give.square >= 0 && take.square >= 0 && board[give.square].group == board[take.square].group) {
        // Both seats retain their original group counts in a within-group swap.
        auto equity = [&](int sq) {
            return (s.mort[sq] ? 0. : board[sq].mortgage) + 2.*board[sq].rents[0];
        };
        my_deeds = equity(take.square) - equity(give.square);
        their_deeds = -my_deeds;
    }
    double mine = my_deeds + cash_gain(s.p[p].cash,-cash);
    double theirs = their_deeds + cash_gain(s.p[1-p].cash,cash);
    double growth_vote = soft_vote(1.12*their_deeds + cash,75);
    double liquidity_vote = soft_vote(.83*their_deeds + cash_gain(s.p[1-p].cash,cash),60);
    double defensive_vote = soft_vote(theirs - .68*std::max(0.,mine),85);
    return {mine,(growth_vote + liquidity_vote + defensive_vote)/3.};
}

State after_deal(const Policy& base,const State& s,int proposer,const Action& deal) {
    State out = s;
    // The same transaction routine used by the game settles both transfers and
    // mortgage interest. Estimate each mortgage election with the public prior.
    settle_trade(out,proposer,deal,base,base);
    return out;
}

TradeAssessment score(const Policy& pol,const State& s,int proposer,const Action& deal,
                      const State& after,float before,double willingness,float refusal) {
    const float next = neural_value(*pol.net,after,proposer);
    const float learned_willingness = response_likelihood(pol,s,proposer,deal);
    // neural_value is antisymmetric by construction.
    TradeAssessment result;
    result.legal = 1;
    result.proposer_before = before;
    result.proposer_after = next;
    result.recipient_before = 1.f-before;
    result.recipient_after = 1.f-next;
    result.acceptance_estimate = learned_willingness >= 0.f ? learned_willingness : float(willingness);
    if (pol.style >= 7) {
        const float generosity = std::clamp(offer_generosity(s,proposer,deal)/400.f,-1.f,1.f);
        const float isolation = std::max(0.f,refusal-.5f);
        const float participation = std::clamp(2.f*(1.f-refusal)*(1.f+isolation*generosity), .025f, 1.25f);
        result.acceptance_estimate = std::min(1.f, result.acceptance_estimate*participation);
    }
    result.expected_gain = result.acceptance_estimate * (next-before);
    return result;
}

struct Candidate {
    Action deal;
    double rank = -std::numeric_limits<double>::infinity();
    double willingness = 0;
};
constexpr int finalists = 14;
using Shortlist = std::array<Candidate,finalists>;

// Reserve one finalist for each family: cash-for-deed, straight swap, and
// cash-adjusted swap. Additional slots retain the highest ranked distinct terms.
int family(const Action& a) {
    if (a.give < 0 || a.take < 0) return 0;
    return a.cash == 0 ? 1 : 2;
}
bool same(const Action& a,const Action& b) {
    return a.give==b.give && a.take==b.take && a.cash==b.cash;
}
void shortlist(Shortlist& list,const Candidate& candidate) {
    const int bucket = family(candidate.deal);
    if (candidate.rank > list[bucket].rank) list[bucket] = candidate;
    for (int i=3;i<finalists;++i) {
        if (candidate.rank <= list[i].rank) continue;
        for (int j=finalists-1;j>i;--j) list[j]=list[j-1];
        list[i]=candidate;
        break;
    }
}

Action no_new_deals(void* context,const State& s,int p,const Query& q) {
    if (q.kind==Kind::Offer) return {};
    return decide(*static_cast<const Policy*>(context),s,p,q);
}

float continuation(const NeuralNet& net,const Policy& rollout,const State& s,int p,uint64_t seed) {
    Game g(seed,rollout,rollout);
    g.s=s;
    // Both branches resume inside the current trade window. The continuation
    // wrapper suppresses proposals so the same offer is not made a second time.
    g.s.turn=std::max(0,s.turn-1);
    condition_decks_on_held_cards(g);
    g.play(g.s.turn+16);
    return neural_value(net,g.s,p);
}

bool selective_negotiator(const State& s,int seat) {
    int proposals=0, refusals=0, acceptances=0;
    const auto& h=s.behavior[seat];
    for (int i=0;i<h.size;++i) {
        const int kind=h.events[i].kind;
        proposals+=kind==ProposedTrade || kind==ProposedBundle;
        refusals+=kind==DeclinedTrade || kind==DeclinedBundle;
        acceptances+=kind==AcceptedTrade || kind==AcceptedBundle;
    }
    return (proposals>=2 && refusals>=1+2*acceptances) || (refusals>=3 && acceptances==0);
}

Action propose(const Policy& pol,const State& s,int p) {
    // The table player can propose on each of its own turns.
    const bool frequent_offers = pol.style == 12;
    if (s.turn < 6 || (!frequent_offers && (s.turn/2)%2 != 0)) return {};
    const float refusal=pol.style >= 7 ? refusal_probability(pol,s,1-p) : .5f;
    // Even a persistent refuser gets occasional probes so a change of behavior is observable.
    if (!frequent_offers && refusal > .82f && (s.turn/2)%8 != 0) return {};
    const bool use_deal_net=pol.deal_net && selective_negotiator(s,1-p);
    Policy base=base_of(pol);
    std::array<Terms,29> give{},take{};
    int gives=1,takes=1;
    for (int sq=0;sq<Squares;++sq) {
        if (s.owner[sq]<0) continue;
        const bool taking = s.owner[sq] != p;
        Action a{1,-1,taking ? -1 : sq,taking ? sq : -1,0};
        // A transfer that requires mortgage interest may still be affordable as
        // part of a cash deal. Defer financial legality until terms are priced.
        if (!legal_trade(s,p,a) && !s.mort[sq]) continue;
        Terms v=deed_terms(base,s,p,sq,taking);
        if (taking) take[takes++]=v; else give[gives++]=v;
    }
    const auto& last = s.rejected[p];
    const bool recent = last.turn>0 && s.turn>=last.turn && s.turn-last.turn<=12;
    Shortlist list;
    for (int i=0;i<gives;++i) for (int j=0;j<takes;++j) {
        const auto& g=give[i]; const auto& t=take[j];
        if (g.square<0 && t.square<0) continue;
        double my_value=g.proposer+t.proposer, their_value=g.recipient+t.recipient;
        double surplus=my_value+their_value;
        int face=(t.square<0 ? 0 : board[t.square].price)-(g.square<0 ? 0 : board[g.square].price);
        // Explore a straight swap, prices around face value, divisions of joint
        // surplus, and an improved counteroffer following a recent refusal.
        const bool retry=recent && g.square==last.give && t.square==last.take;
        const double improved=retry ? last.cash+std::max(15,std::abs(last.cash)/10+10) : 0.;
        const double cash_options[]={0.,.70*face,1.30*face,double(face),improved,-their_value+.12*surplus,
                                     -their_value+.40*surplus,-their_value+.72*surplus};
        int seen[8], nseen=0;
        for (double amount : cash_options) {
            int cash = std::clamp(int(std::round(amount/5.))*5,-s.p[1-p].cash,s.p[p].cash);
            if (retry && cash<=last.cash) continue;
            if (std::find(seen,seen+nseen,cash)!=seen+nseen) continue;
            seen[nseen++]=cash;
            Action deal{1,-1,g.square,t.square,cash};
            if (!legal_trade(s,p,deal)) continue;
            const auto m=market(s,p,g,t,cash);
            if (m.mine <= 0 || m.acceptance < .13) continue;
            shortlist(list,{deal,m.mine*m.acceptance,m.acceptance});
        }
    }
    float before=-1.f, best=.0005f;
    Action result{};
    for (int i=0;i<finalists;++i) {
        if (!std::isfinite(list[i].rank)) continue;
        bool duplicate=false;
        for (int j=0;j<i;++j) duplicate|=same(list[j].deal,list[i].deal);
        if (duplicate) continue;
        if (before<0) before=neural_value(*pol.net,s,p);
        const auto next=after_deal(base,s,p,list[i].deal);
        const auto assessment=score(pol,s,p,list[i].deal,next,before,list[i].willingness,refusal);
        float rank=assessment.expected_gain;
        if (use_deal_net) {
            const auto x=deal_features(pol,s,next,1-p,list[i].deal);
            const float gain=.25f*(assessment.proposer_after-before)-.75f*recipient_gain(*pol.deal_net,x);
            // A value-based rival can screen our proposals using its own board estimate.
            const float reply_weight=std::clamp(.5f+(before-assessment.proposer_after)*13.f,.03f,.97f);
            rank=assessment.acceptance_estimate*gain*(.18f+1.64f*reply_weight);
        }
        if (rank > best) {
            best=rank;
            result=list[i].deal;
        }
    }
    if (result.value) {
        // Check the leading deal against plausible short-term rent and building
        // outcomes. Both branches see the same six sampled futures.
        Policy rollout=base;
        rollout.external=no_new_deals;
        rollout.context=&base;
        const auto after=after_deal(base,s,p,result);
        double delta=0;
        for (int i=0;i<6;++i) {
            uint64_t seed=12587771ULL*(i+15)+s.turn;
            delta+=continuation(*pol.net,rollout,after,p,seed)-continuation(*pol.net,rollout,s,p,seed);
        }
        const float immediate=neural_value(*pol.net,after,p)-before;
        double advantage=.20*immediate+.80*delta/6;
        if (use_deal_net) {
            const auto x=deal_features(pol,s,after,1-p,result);
            advantage=.35*advantage-.65*recipient_gain(*pol.deal_net,x);
        }
        if (advantage < .001) return {};
    }
    return result;
}

Action receive(const Policy& pol,const State& s,int p,const Action& deal) {
    if (!legal_trade(s,1-p,deal)) return {};
    Policy base=base_of(pol);
    State after=after_deal(base,s,1-p,deal);
    double advantage=neural_value(*pol.net,after,p)-neural_value(*pol.net,s,p);
    if (std::abs(advantage) < .17) {
        // Near the value boundary, test development, rent exposure and insolvency
        // over sixteen turns. Both branches draw from the same independent seeded
        // streams and uniform unknown deck orders, conditioned on held cards.
        Policy rollout=base;
        rollout.external=no_new_deals;
        rollout.context=&base;
        Rng rng{uint64_t(s.turn)*0xd6e8feb86659fd93ULL + uint64_t(s.p[0].pos*40+s.p[1].pos)};
        double paired=0;
        constexpr int trials=4;
        for (int i=0;i<trials;++i) {
            auto seed=rng.next();
            paired+=continuation(*pol.net,rollout,after,p,seed)-continuation(*pol.net,rollout,s,p,seed);
        }
        advantage=.55*advantage+.45*paired/trials;
    }
    if (pol.deal_net && selective_negotiator(s,1-p)) {
        const auto x=deal_features(pol,s,after,p,deal);
        advantage=.30*advantage+.70*recipient_gain(*pol.deal_net,x);
    }
    // Reject ties and very small apparent gains that depend on approximation.
    return {advantage > .0015};
}
} // namespace

TradeAssessment assess_trade(const Policy& pol,const State& s,int proposer,const Action& deal) {
    if (!pol.net || !legal_trade(s,proposer,deal)) return {};
    auto base=base_of(pol);
    auto m=market(s,proposer,deed_terms(base,s,proposer,deal.give,false),
                  deed_terms(base,s,proposer,deal.take,true),deal.cash);
    const auto after=after_deal(base,s,proposer,deal);
    auto assessment=score(pol,s,proposer,deal,after,neural_value(*pol.net,s,proposer),m.acceptance,
                          pol.style >= 7 ? refusal_probability(pol,s,1-proposer) : .5f);
    if (pol.deal_net) {
        auto x=deal_features(pol,s,after,1-proposer,deal);
        assessment.recipient_deal_gain=recipient_gain(*pol.deal_net,x);
        assessment.recipient_uses_deal_model=selective_negotiator(s,proposer);
    }
    return assessment;
}

Action neural_trade(const Policy& pol,const State& s,int who,const Query& q) {
    if (!pol.net || (who!=0 && who!=1)) return {};
    if (q.kind==Kind::Offer) return propose(pol,s,who);
    if (q.kind==Kind::Accept) return receive(pol,s,who,{1,-1,q.give,q.take,q.cash});
    return {};
}
} // namespace mono
