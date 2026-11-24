#include "neural.hpp"
#include "../groups.hpp"
#include "../neural_trade.hpp"
#include <cmath>
#include <limits>

namespace mono::four {
namespace {
using Features = std::array<double,7>;

// The same hidden units transform the acquisition and negotiation feature
// vectors. Each action family has its own readout. The encoder starts near
// the identity, allowing policy search to tune single economic effects as
// well as interactions without fitting a fresh value network from scratch.
double forward(const double* w, const Features& input, int head) {
    double out = w[head];
    for (int h = 0; h < 7; ++h) {
        const double mix = .5 * input[(h+1)%7] - .35 * input[(h+3)%7];
        const double z = 1.4 * input[h] + (.35+w[23])*mix + w[16+h];
        out += w[head+1+h] * (z / (1.+std::abs(z)));
    }
    return std::clamp(out,-5.,5.);
}

struct Table {
    int counts[Seats][Groups]{};
    std::array<int,Seats> capital{}, peak_rent{};
    Table(const Game& g) {
        for (int p=0;p<Seats;++p) capital[p]=g.players[p].cash;
        for (int sq=0;sq<Squares;++sq) {
            const int p=g.owner[sq];
            if (p<0) continue;
            const auto& d=board[sq];
            ++counts[p][d.group];
            capital[p]+=d.mortgage*(!g.mort[sq])+g.level[sq]*d.house/2;
            if (g.level[sq]) peak_rent[p]=std::max(peak_rent[p],d.rents[g.level[sq]]);
        }
    }
};

int greatest_rent(const Table& t, int who) {
    int high=0;
    for (int p=0;p<Seats;++p) if (p!=who) high=std::max(high,t.peak_rent[p]);
    return high;
}

Features acquisition(const Game& g,const Table& t,int who,int sq) {
    const auto& d=board[sq];
    const int group=d.group, ours=t.counts[who][group];
    int opposition=0, opponents_with_deed=0, richest=0;
    for (int p=0;p<Seats;++p) {
        if (p==who || !g.alive[p]) continue;
        opposition=std::max(opposition,t.counts[p][group]);
        opponents_with_deed+=t.counts[p][group]>0;
        richest=std::max(richest,g.players[p].cash);
    }
    const double own_progress=double(ours)/(group_size[group]-1);
    const double rival_progress=double(opposition)/(group_size[group]-1);
    // A third house produces most of a street's rent increase.
    const double efficiency=group<Rail ? d.rents[3]/(3.*d.house)-1. : group==Rail?.5:-.6;
    return {own_progress,rival_progress,efficiency,
            std::clamp((g.players[who].cash-d.price)/600.,0.,2.),
            1.-opponents_with_deed, greatest_rent(t,who)/650.,
            (g.players[who].cash-richest)/750.};
}

// Cash-supported rental potential of a group. Counting all foreign owners
// distinguishes a clean path to completion from a group split among rivals.
double group_value(const Game& g,const Table& t,int owner,int group,
                   int count,int rivals,const int cash) {
    if (count<=0) return 0.;
    if (group==Rail) return 75.*count+30.*(1<<count)*(g.living()-1);
    if (group==Utility) return count==2?185.:70.;
    const int sq=group_squares[group][0];
    const auto& deed=board[sq];
    if (count==group_size[group]) {
        const double capacity=std::clamp((cash+.28*(t.capital[owner]-g.players[owner].cash))/
                                         (3.*deed.house*group_size[group]),.18,1.3);
        return (220.+1.25*deed.rents[3])*(.6+.23*g.living())*capacity;
    }
    // One counterparty can supply a missing group. Multiple holders must
    // separately agree, reducing the option value of a partly assembled set.
    const double bargaining=rivals==0?120.:rivals==1?70.:25.;
    return count*bargaining+((count+1==group_size[group])?(rivals?110.:230.):0.);
}

struct DealValues {
    double own_progress=0., partner_progress=0., external_progress=0.;
};

// Each affected color is evaluated once before and after the deal. This also
// catches changes in the prospects of opponents who receive neither deed.
DealValues deal_values(const Game& g,const Table& t,int who,int rival,const Action& deal) {
    DealValues value;
    const int give=deal.give<0?-1:board[deal.give].group;
    const int take=deal.take<0?-1:board[deal.take].group;
    if (give==take) return value;
    for (const int group : {give,take}) {
        if (group<0) continue;
        const int delta=group==take?1:-1;
        int before_owners=0, after_owners=0;
        std::array<int,Seats> after{};
        for (int p=0;p<Seats;++p) {
            const int before=t.counts[p][group];
            after[p]=before+(p==who?delta:p==rival?-delta:0);
            before_owners+=before>0;
            after_owners+=after[p]>0;
        }
        for (int p=0;p<Seats;++p) {
            if (!g.alive[p]) continue;
            const int old=t.counts[p][group], cash=g.players[p].cash;
            const int next_cash=cash+(p==who?-deal.cash:p==rival?deal.cash:0);
            const double before=group_value(g,t,p,group,old,before_owners-(old>0),cash);
            const double next=group_value(g,t,p,group,after[p],after_owners-(after[p]>0),next_cash);
            if (p==who) value.own_progress+=next-before;
            else if (p==rival) value.partner_progress+=next-before;
            else value.external_progress+=next-before;
        }
    }
    return value;
}

double trade_residual(const Game& g,const Table& t,int who,int rival,const Action& offer) {
    const DealValues delta=deal_values(g,t,who,rival,offer);
    auto equity=[&](int sq) { return sq<0?0:board[sq].price-(g.mort[sq]?board[sq].mortgage:0); };
    int outsider_capital=0;
    for (int p=0;p<Seats;++p)
        if (p!=who && p!=rival && g.alive[p]) outsider_capital=std::max(outsider_capital,t.capital[p]);
    const double rival_pressure=std::clamp((t.capital[rival]-outsider_capital)/1100.,-1.,1.);
    const double cash_pressure=std::clamp((400.+.35*greatest_rent(t,who)-g.players[who].cash)/450.,-1.,1.);
    const Features x={delta.own_progress/700.,delta.partner_progress/700.,
                      (equity(offer.take)-equity(offer.give)-offer.cash)/400.,
                      -offer.cash/500.,delta.external_progress/180.,
                      rival_pressure*delta.partner_progress/600.,-offer.cash*cash_pressure/350.};
    return forward(g.multiplayer_weights[who],x,8);
}

bool enabled_head(const double* w,int head) {
    return w && std::any_of(w+head,w+head+8,[](double v){return v!=0.;});
}
} // namespace

bool multiplayer_enabled(const double* w) {
    return enabled_head(w,0) || enabled_head(w,8);
}
bool multiplayer_trades_enabled(const double* w) { return enabled_head(w,8); }

Action multiplayer_action(const Game& g,int who,int rival,const State& pair,const Query& q,Action base) {
    const double* w=g.multiplayer_weights[who];
    if (q.kind==Kind::Accept) {
        if (!enabled_head(w,8)) return base;
        const Action proposed{1,-1,q.give,q.take,q.cash};
        if (!legal_trade(pair,1,proposed)) return {};
        const Action received{1,-1,q.take,q.give,-q.cash};
        const double correction=trade_residual(g,Table(g),who,rival,received);
        // The duel model's final vote includes its counterfactual rollouts;
        // keep a margin around it rather than flipping every near tie.
        return {(base.value==1?.50:-.50)+correction>0.};
    }
    if ((q.kind!=Kind::Buy && q.kind!=Kind::Bid) || q.square<0 || q.square>=Squares ||
        board[q.square].group<0 || q.amount<0 || !enabled_head(w,0)) return base;
    const int face=board[q.square].price;
    const double residual=forward(w,acquisition(g,Table(g),who,q.square),0);
    const double anchor=q.kind==Kind::Buy?face*(base.value?1.20:.80):base.value;
    const double ceiling=(anchor+.25*face)*std::exp2(.85*residual)-.25*face;
    if (q.kind==Kind::Buy) return {ceiling>=face && g.players[who].cash>=face};
    return {std::clamp(int(std::round(ceiling)),0,g.players[who].cash)};
}

OfferChoice multiplayer_offer(const Game& g,int who,int rival,const State& pair,Action base) {
    const Policy& policy=g.policies[who];
    const Table table(g);
    OfferChoice best{{},0.};
    auto consider=[&](const Action& candidate) {
        if (!legal_trade(pair,0,candidate)) return;
        const auto eval=assess_trade(policy,pair,0,candidate);
        const double residual=trade_residual(g,table,who,rival,candidate);
        const double willingness=policy.net?eval.acceptance_estimate:.65;
        const double score=eval.expected_gain+.14*residual*std::max(.05,willingness);
        if (score>best.score) best={candidate,score};
    };
    consider(base);
    // Bid directly for the final deed in a color. Following a refusal,
    // improve the price with public counterparty history and available cash.
    if (pair.turn<6 || (pair.turn/2)%2) return best;
    const auto& previous=g.rejected[who][rival];
    for (int sq=0;sq<Squares;++sq) {
        if (g.owner[sq]!=rival) continue;
        const auto& deed=board[sq];
        if (deed.group>=Rail || table.counts[who][deed.group]+1!=group_size[deed.group]) continue;
        const int fee=g.mort[sq]?(deed.mortgage+9)/10:0;
        const int budget=g.players[who].cash-2*deed.house-fee;
        if (budget<deed.price) continue;
        const int initial=(previous.take==sq && previous.give<0)?
            std::max(deed.price,previous.cash+std::max(20,previous.cash/5)):deed.price;
        if (initial>budget) continue;
        for (double premium : {1.,1.55,2.25}) {
            const int price=std::min(budget,int(std::round(initial*premium/5.))*5);
            consider({1,-1,-1,sq,price});
            if (price==budget) break;
        }
    }
    return best;
}
} // namespace mono::four
