#include "bid_trace.hpp"
#include "adaptive_policy.hpp"
#include "groups.hpp"
#include "neural_trade.hpp"
#include "neural_value.hpp"
#include "opponent.hpp"
#include <cmath>
#include <limits>

namespace mono {
const std::array<const char*,ResilienceParams> resilience_names={
    "cold_population_weight","alternative_population_weight","neural_bid_mix","premium",
    "denial_multiplier","purchase_margin","neural_equity_mix","downside_discount"
};
const std::array<const char*,SpecialistParams> specialist_names={
    "deed_collateral","uncontested_start","set_progress","completed_set","rival_set",
    "split_deed","railroad","railroad_growth","utility","cash_pressure",
    "price_pressure","opening_cash","building_cash","first_option","bidder_prior",
    "bidder_variation","neural_equity","neural_scale","trade_switch","market_caution"
};
namespace {
using Weights=std::array<double,SpecialistParams>;
enum Gene {Collateral,First,Progress,Finish,Block,Split,RailValue,RailGrowth,UtilValue,
           CashPressure,PricePressure,OpenFloat,BuildFloat,FirstOption,BidderPrior,
           Variation,ValueWeight,ValueScale,TradeSwitch,Caution};
int market_size(const State& s) {
    int open=28;
    for(int g=0;g<Groups;++g) open-=s.count[0][g]+s.count[1][g];
    return open;
}
float cash_cost(int cash,int cost,double caution) {
    // The last dollars may be needed for rent or to exercise a future purchase option.
    const float low=std::max(0,350-(cash-cost))-std::max(0,350-cash);
    return cost+float(caution)*low;
}
double deed_value(const State& s,int buyer,int sq,const Weights& w,bool rival) {
    const auto& d=board[sq];
    int g=d.group,n=s.count[buyer][g],other=s.count[1-buyer][g];
    if(g==Rail) {
        const int group_rent=25<<std::min(n,3);
        return d.mortgage+(70.+250*w[RailValue])+(group_rent*(n+1)-25*n)*(1.+4.5*w[RailGrowth]);
    }
    if(g==Utility) return d.mortgage+(30.+120*w[UtilValue])*(n?1.9:1.);
    double v=d.mortgage*(.75+1.1*w[Collateral])+d.rents[0]*(1.5+2.*w[Collateral]);
    if(n+1==group_size[g]) {
        int third=0;
        for(int i=0;i<group_size[g];++i) third+=board[group_squares[g][i]].rents[3];
        const double capital=.65+.35*std::min(1.,(s.p[buyer].cash+d.mortgage)/(5.*d.house));
        const double multiplier=rival ? w[Block] : w[Finish];
        return v+capital*(.3+1.5*multiplier)*(third-1.2*d.house*group_size[g]);
    }
    if(!other) return v+(n?100.+490*w[Progress]:30.+320*w[First]);
    if(n) return v*(.55+.8*w[Split]);
    return v+40.+150*w[First];
}
void acquire(State& after,int seat,int sq,int cost) {
    after.owner[sq]=seat;
    ++after.count[seat][board[sq].group];
    after.p[seat].cash-=cost;
}
struct Bidder {int ceiling; double mass;};
struct Market {
    std::array<Bidder,13> bidders{};
    double ours,rival;
    const Weights& w;
    int cash,enemy_cash;
    int scenarios=8;
    double win_at(int price) const {return ours-cash_cost(cash,price,2.*w[CashPressure]);}
    double lose_at(int price) const {
        return (.3+1.4*w[PricePressure])*cash_cost(enemy_cash,price,1.4*w[CashPressure])-rival;
    }
    double utility(int bid) const {
        double total=0;
        for(int i=0;i<scenarios;++i) {
            const auto& b=bidders[i];
            double outcome=0;
            if(bid<10 && b.ceiling<10) outcome=0;
            else if(bid==b.ceiling) outcome=(win_at(bid)+lose_at(bid))*.5;
            else if(bid>b.ceiling) outcome=win_at(std::max(10,b.ceiling+1));
            else outcome=lose_at(std::max(10,bid+1));
            total+=b.mass*outcome;
        }
        return total;
    }
    std::pair<int,double> best_bid() const {
        // A two-bidder proxy auction only changes winner at a rival ceiling. Just below
        // it, the rival wins but spends its full valuation; just above it, we win.
        int best_bid=0;
        double best=utility(0);
        auto consider=[&](int bid) {
            bid=std::clamp(bid,0,cash);
            double score=utility(bid);
            if(score>best+1e-5 || (std::abs(score-best)<1e-5 && bid<best_bid)) {best=score;best_bid=bid;}
        };
        consider(10);consider(cash);
        for(int i=0;i<scenarios;++i) {
            const auto& b=bidders[i];consider(b.ceiling-1);consider(b.ceiling);consider(b.ceiling+1);}
        return {best_bid,best};
    }
};
struct QuoteEvidence {double match=0,scale=1;int observations=0;};

// Infer the earlier group counts from later public purchases. Stop at a completed trade,
// which may have changed ownership without a landing. Earlier cash is deliberately unknown.
int uncapped_blocker_price(int sq,int own,int enemy) {
    const auto& d=board[sq];int g=d.group;
    if(g==Rail) return 210+105*own+(enemy>=2?110:0);
    if(g==Utility) return own?200:120;
    const bool complete=own+1==group_size[g],block=enemy+1==group_size[g];
    if(complete || block) {
        int rents=0;
        for(int k=0;k<group_size[g];++k) rents+=board[group_squares[g][k]].rents[3];
        int surplus=std::max(0,rents-group_size[g]*d.house);
        return d.price+(complete?surplus*2/3:surplus/2);
    }
    if(!enemy) return d.price+(own?d.price*2/3:90);
    return !own?d.price+65:d.price*3/4;
}
// The benchmark blocker never proposes or accepts a trade. Its bought deeds can
// only accumulate. Past counts were no greater than today's, less the current
// acquisition. Use only observations that are impossible under these bounds.
bool different_bidder(const State& s,int who) {
    const int other=1-who;
    for(int owner=0;owner<2;++owner) {
        const auto& history=s.behavior[owner];
        for(int i=history.size-1;i>=0;--i) {
            const auto& e=history.events[i];
            if(owner==other && (e.kind==ProposedTrade || e.kind==AcceptedTrade)) return true;
            if(e.kind!=WonAuction && !(owner==other && e.kind==BoughtAtFace)) continue;
            if(e.square<0 || e.square>=Squares || board[e.square].group<0) continue;
            int group=board[e.square].group;
            int a=s.count[other][group]-(owner==other), b=s.count[who][group]-(owner==who);
            int upper=0;
            for(int n=0;n<=a;++n) for(int rival=0;rival<=b;++rival)
                if(n+rival<group_size[group]) upper=std::max(upper,uncapped_blocker_price(e.square,n,rival));
            if(e.amount>upper+(owner==who)) return true;
        }
    }
    return false;
}

QuoteEvidence observed_prices(const State& s,int who) {
    QuoteEvidence info;
    int last_trade=-1;
    for(const auto& history:s.behavior) for(int i=0;i<history.size;++i)
        if(history.events[i].kind==AcceptedTrade) last_trade=std::max(last_trade,history.events[i].turn);
    std::array<double,6> ratios{};int nr=0;
    for(int rel=0;rel<2;++rel) {
        const auto& history=s.behavior[rel?1-who:who];
        for(int i=history.size-1;i>=0;--i) {
            const auto& e=history.events[i];
            if(e.turn<=last_trade) break;
            if(e.square<0 || e.square>=Squares || board[e.square].group<0) continue;
            bool purchase=e.kind==BoughtAtFace,auction=e.kind==WonAuction;
            if((!auction && !(rel && purchase)) || e.amount<=10) continue;
            int g=board[e.square].group,counts[]={s.count[0][g],s.count[1][g]};
            for(int p=0;p<2;++p) {
                const auto& h=s.behavior[p];
                for(int k=h.size-1;k>=0 && h.events[k].turn>=e.turn;--k) {
                    const auto& later=h.events[k];
                    if((later.kind==BoughtAtFace || later.kind==WonAuction) && later.square>=0 &&
                       later.square<Squares && board[later.square].group==g) --counts[p];
                }
            }
            int model=uncapped_blocker_price(e.square,std::max(0,counts[1-who]),std::max(0,counts[who]));
            if(rel) {
                // A bidder that won or paid face must have been willing to spend this much.
                if(e.amount>model+2) info.match-=1.45;
            } else {
                // When we win, the public clearing price exposes the losing bid to within $1.
                // A much lower price may simply reflect the rival's unknown historical cash.
                double bid=e.amount-1;
                if(std::abs(bid-model)<=2) info.match+=1.1;
                else if(bid>model+12) info.match-=1.1;
                else if(bid>.73*model && model-bid>12) info.match-=.65;
                if(nr<int(ratios.size()) && bid>.60*model && bid<1.65*model)
                    ratios[nr++]=bid/model;
                ++info.observations;
            }
        }
    }
    if(nr>=2) {
        std::sort(ratios.begin(),ratios.begin()+nr);
        info.scale=std::clamp(ratios[nr/2],.70,1.45);
    }
    return info;
}

Market auction_model(const Policy& pol,const State& s,int who,int sq,double refusal,double aggression,const QuoteEvidence& evidence,const ResilienceWeights* extra,bool gated) {
    const auto& w=pol.specialist->w;
    int other=1-who;
    double own=deed_value(s,who,sq,w,false),opp=deed_value(s,other,sq,w,true);
    if(pol.net) {
        float before=neural_value(*pol.net,s,who);
        State win=s,loss=s;
        int our_cost=std::min(s.p[who].cash,board[sq].price);
        int their_cost=std::min(s.p[other].cash,board[sq].price);
        acquire(win,who,sq,our_cost); acquire(loss,other,sq,their_cost);
        double scale=600.+3200*w[ValueScale],mix=.06+.49*w[ValueWeight];
        own=(1-mix)*own+mix*(our_cost+scale*(neural_value(*pol.net,win,who)-before));
        opp=(1-mix)*opp+mix*(their_cost+scale*(before-neural_value(*pol.net,loss,who)));
    }
    Market m{{},own,opp,w,s.p[who].cash,s.p[other].cash};
    int blocker=holdout(s,other,{Kind::Bid,sq,10}).value;
    Policy historical=pol;historical.style=0;historical.external=nullptr;
    double focus=std::clamp(.05+.73*w[BidderPrior]+.55*(refusal-.5),.03,.96);
    focus=std::clamp(focus+.08*std::clamp(evidence.match,-4.,4.),.03,.97);
    double spread=.06+.34*w[Variation];
    auto bounded=[&](double price){return std::clamp(int(std::round(price)),0,s.p[other].cash);};
    double scaled=evidence.observations>=2 && std::abs(evidence.scale-1)>.015 ? .34 : 0.;
    m.bidders[7]={bounded(blocker*evidence.scale),focus*scaled};
    m.bidders[0]={blocker,focus*(1.-spread-scaled)};
    m.bidders[1]={bounded(blocker*(.70+.12*aggression)),focus*spread*.5};
    m.bidders[2]={bounded(blocker*(1.12+.24*aggression)),focus*spread*.5};
    for(int i=0;i<4;++i) {
        Policy type=i?preset(i):historical;
        m.bidders[i+3]={decide(type,s,other,{Kind::Bid,sq,10}).value,(1.-focus)*.25};
    }
    if(pol.net && extra) {
        const auto& x=extra->w;
        m.scenarios=gated?13:12;
        const bool changed=refusal<.25*w[TradeSwitch] || evidence.match<-.55-2.2*w[TradeSwitch];
        double uncertainty=std::clamp((.48-refusal)*1.8 - .20*evidence.match,0.,1.);
        double mass=std::clamp(x[0]*(1.-std::clamp(evidence.match*.35,0.,.9))
                        +(x[1]-x[0])*(changed?1:uncertainty),0.,.97);
        Policy neural=pol;neural.style=6;neural.external=nullptr;
        int observed=neural_decide(neural,s,other,{Kind::Bid,sq,10}).value;
        bool denial=s.count[who][board[sq].group]+1==group_size[board[sq].group];
        auto model_price=[&](double mix,double premium,double deny,int reserve) {
            double price=mix*observed+(1.-mix)*blocker;
            return std::clamp(int(price*premium*(denial?deny:1.)),0,std::max(0,s.p[other].cash-reserve));
        };
        for(int k=0;k<8;++k)m.bidders[k].mass*=1.-mass;
        double mix=x[2],premium=x[3];
        m.bidders[8]={model_price(mix,premium,x[4],10),mass*.44};
        m.bidders[9]={model_price(.95,premium*1.17,x[4]*1.2,25),mass*.20};
        m.bidders[10]={model_price(.35,premium*.89,.92,65),mass*.18};
        m.bidders[11]={model_price(.80,1.0,1.,30),mass*(gated?.10:.18)};
        const auto& deed=board[sq];
        int g=deed.group,owned=s.count[other][g],rival_owned=s.count[who][g];
        double income=.56*observed+.44*deed.price+deed.mortgage*.35;
        if(g==Rail) income+=70+55*owned;
        else if(g<Rail) {
            income+=owned*(40+deed.rents[0]);
            if(owned+1==group_size[g]) income+=deed.rents[3]*.52;
            else if(rival_owned+1==group_size[g]) income+=deed.rents[3]*.36;
        }
        int cash_reserve=std::min(170,35+s.p[other].cash/14);
        m.bidders[12]={std::clamp(int(income),0,std::max(0,s.p[other].cash-cash_reserve)),mass*.08};
        if(x[6]>0) {
            int my_valuation=neural_decide(neural,s,who,{Kind::Bid,sq,10}).value;
            double a=x[6]*mass;
            m.ours=(1.-a)*m.ours+a*my_valuation;
            m.rival=(1.-a)*m.rival+a*observed;
        }
    }
    return m;
}
Action opening_finance(const State& s,int who,int target) {
    double cheapest=std::numeric_limits<double>::infinity();Action a;
    for(int g=0;g<Groups;++g) {
        if(!s.count[who][g] || (g<Rail && s.count[who][g]==group_size[g])) continue;
        for(int i=0;i<group_size[g];++i) {
            int sq=group_squares[g][i];
            if(!can_mortgage(s,who,sq)) continue;
            double price=(1.+rent(s,sq,7))/board[sq].mortgage;
            if(g==Rail && s.count[who][g]>=2) price+=.8;
            // Keep unimproved collateral flexible while funding a specific purchase window.
            if(board[sq].mortgage+s.p[who].cash>target+100) price+=.025;
            if(price<cheapest) {cheapest=price;a={Mortgage,sq};}
        }
    }
    return a;
}
}
static Action decide_with_market(const Policy& pol,const State& s,int who,const Query& q,
                                 const ResilienceWeights* extra,bool gated) {
    Policy base=pol;base.style=0;base.external=nullptr;base.context=nullptr;
    if(who<0 || who>1) return {};
    auto normal=[&]() {return pol.net?neural_decide(pol,s,who,q):decide(base,s,who,q);};
    if(!pol.specialist) return normal();
    if(q.kind==Kind::Offer || q.kind==Kind::Accept)
        return pol.net?neural_trade(pol,s,who,q):decide(base,s,who,q);
    bool property=q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0);
    if(!property && (q.kind!=Kind::Manage || q.square>=0)) return normal();
    const auto& w=pol.specialist->w;
    if(!property && !market_size(s)) return normal();
    double refusal=refusal_probability(pol,s,1-who);
    if(refusal<.25*w[TradeSwitch] && (!extra || !property)) return normal();
    if(property) {
        if(q.square<0 || q.square>=Squares || board[q.square].group<0 || s.owner[q.square]>=0) return {};
        if(extra && gated && !different_bidder(s,who))
            return decide_with_market(pol,s,who,q,nullptr,false);
        auto evidence=observed_prices(s,who);
        if(!extra && evidence.match<-.55-2.2*w[TradeSwitch]) return normal();
        auto m=auction_model(pol,s,who,q.square,refusal,opponent_aggressiveness(pol,s,1-who),evidence,extra,gated);
        auto chosen=m.best_bid();
        if(q.kind==Kind::Bid) {
            if(current_bid_trace) explain_bid("Maximized expected utility across modeled opponent ceilings; near-ties favor the lower bid.",
                {{"chosen_score",chosen.second},{"pass_score",m.utility(0)},{"minimum_bid_score",m.utility(10)},
                 {"face_price_score",m.utility(std::min(board[q.square].price,m.cash))},
                 {"own_deed_value",m.ours},{"opponent_deed_value",m.rival}});
            return {chosen.first};
        }
        int price=board[q.square].price;
        if(s.p[who].cash<price) return {};
        // A purchase on landing avoids model risk. The safety margin can itself be
        // learned from match outcomes and shrinks when the other seat cannot pay face.
        double option=s.p[1-who].cash>=price ? -90.+230.*w[FirstOption] : -30.;
        double near_rival=0;
        for(int i=0;i<m.scenarios;++i) {
            const auto& b=m.bidders[i];
            near_rival+=b.mass*(b.ceiling>=price);
        }
        if(extra) {
            const auto& x=extra->w;
            double mismatch=std::clamp(.55-refusal-.18*evidence.match,0.,1.);
            option+=x[5]*mismatch;
            double ours_worst=std::min(0.,m.win_at(price));
            double discount=(1.-std::clamp(evidence.match*.25,0.,1.))*x[7];
            return {m.win_at(price)+option*near_rival >= chosen.second+discount*ours_worst};
        }
        return {m.win_at(price)+option*near_rival >= chosen.second};
    }
    int open=market_size(s);
    if(!open) return normal();
    int target=int((100.+780.*w[OpenFloat])*(.35+.65*std::min(open,12)/12.));
    bool complete=false;
    for(int g=0;g<Rail;++g) complete|=s.count[who][g]==group_size[g];
    if(complete) target=int(target*(.15+.7*w[BuildFloat]));
    if(s.p[who].cash<target && (!complete || s.p[who].cash<120)) {
        auto loan=opening_finance(s,who,target);
        if(loan.value==Mortgage) return loan;
    }
    Action action=normal();
    if(action.value==Redeem && board[action.square].group>=0 &&
       s.count[who][board[action.square].group]!=group_size[board[action.square].group] &&
       s.p[who].cash-board[action.square].mortgage*11/10<target+70) return {};
    if(action.value==Build && open>2 && s.level[action.square]>=2 &&
       s.p[who].cash-board[action.square].house<target*w[Caution]) return {};
    return action;
}
Action adaptive_decide(const Policy& pol,const State& s,int who,const Query& q) {
    return decide_with_market(pol,s,who,q,nullptr,false);
}
Action resilience_trial(const Policy& pol,const State& s,int who,const Query& q,bool require_evidence) {
    Policy incumbent=pol;
    incumbent.style=7;
    return decide_with_market(incumbent,s,who,q,pol.resilience,require_evidence);
}
Action resilient_decide(const Policy& pol,const State& s,int who,const Query& q) {
    return resilience_trial(pol,s,who,q,true);
}
} // namespace mono
