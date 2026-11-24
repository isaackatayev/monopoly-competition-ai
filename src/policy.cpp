#include "robust_policy.hpp"
#include "neural_trade.hpp"
#include "game.hpp"
#include "groups.hpp"
#include <cmath>
#include <limits>

namespace mono {
const std::array<const char*,Params> param_names = {
    "acquisition", "completion", "denial", "railroads", "auction", "reserve",
    "development", "financing", "trade_demand", "trade_defense", "jail_patience", "house_scarcity"
};
Policy preset(int style) {
    Policy p;
    p.style=style;
    p.w={.66,.70,.77,.64,.57,.33,.78,.76,.46,.77,.61,.53};
    if (style==1) p.w={.85,.36,.25,.90,.60,.28,.38,.32,.25,.22,.30,.20};
    if (style==2) p.w={.62,.88,.45,.42,.55,.34,.90,.61,.38,.45,.61,.50};
    if (style==3) p.w={.30,.55,.40,.48,.36,.85,.50,.16,.56,.71,.65,.30};
    return p;
}

namespace {
enum Gene { Acquire, Complete, Deny, Rails, Auction, Reserve, Develop, Finance, Demand, Defense, JailPatience, Scarcity };
// Approximate resolved landings per thousand rolls, from a fixed-seed walk with immediate jail exit.
constexpr int landing[Squares]={
    31,21,19,22,23,30,23,8,23,23,59,27,26,24,25,29,28,26,29,31,
    29,28,11,27,32,31,27,27,28,26,0,27,27,24,25,25,9,22,22,26
};
constexpr double set_value[8]={490,1050,1040,1450,1280,1200,1160,1180};
int mortgage_fee(int q) {return (board[q].mortgage+9)/10;}

// Capital the player can deploy immediately influences how much an additional monopoly is worth.
double marginal(const Policy& pol,const State& s,int p,int q,int previously,int opposed) {
    const auto& d=board[q];
    const int g=d.group;
    const double acquire=.65+.95*pol.w[Acquire];
    if(g==Rail) {
        constexpr int marginal_rail[]={155,235,355,640};
        return acquire*(.60+.85*pol.w[Rails])*marginal_rail[std::clamp(previously,0,3)];
    }
    if(g==Utility) return acquire*(previously?190:105);
    double base=d.mortgage+acquire*(36+.22*d.price);
    if(previously+1==group_size[g]) {
        double capital=.58+.42*std::clamp(s.p[p].cash/(7.0*d.house),0.,1.);
        base+=set_value[g]*(.33+.95*pol.w[Complete])*capital;
    } else if(previously==0 && opposed==0) {
        base+=acquire*(65+.05*set_value[g]);
    } else if(previously>0 && opposed==0) {
        base+=acquire*(95+.10*set_value[g]);
    }
    return base;
}

int price_limit(const Policy& pol,const State& s,int p,int q) {
    const auto& d=board[q];
    const int g=d.group, self=s.count[p][g], other=s.count[1-p][g], cash=s.p[p].cash;
    double value;
    if(pol.style==0) {
        value=marginal(pol,s,p,q,self,other);
        if(g<Rail && other==group_size[g]-1)
            value+=set_value[g]*(.30+1.0*pol.w[Deny]);
        else if(g==Rail && other>=2)
            value+=(other==3?380.:120.)*pol.w[Deny];
        else if(g<Rail && !self && other)
            value+=(45+.07*set_value[g])*pol.w[Deny];
    } else {
        double mult=pol.style==1?1.35:pol.style==2?1.19:.98;
        value=d.price*mult;
        if(g<Rail && self+1==group_size[g]) value+=set_value[g]*(pol.style==2?.80:.44);
        if(g<Rail && other+1==group_size[g]) value+=set_value[g]*(pol.style==2?.33:.20);
        if(g==Rail) value+=(pol.style==1?1.15:.80)*(45+70*self+20*other);
    }
    value*=.75+.53*pol.w[Auction];
    const bool crucial=g<Rail && (self+1==group_size[g] || other+1==group_size[g]);
    const int buffer=static_cast<int>((crucial?.10:1.0)*(25+150*pol.w[Reserve]));
    return std::clamp(static_cast<int>(value),0,std::max(0,cash-buffer));
}

bool has_color(const State& s,int p) {
    for(int g=0;g<Rail;++g) if(s.count[p][g]==group_size[g]) return true;
    return false;
}
// What the other seat can earn if we release four houses by buying a hotel.
double released_house_income(const State& s,int enemy) {
    double best=0;
    for(int g=0;g<Rail;++g) {
        if(s.count[enemy][g]!=group_size[g]) continue;
        int houses=0;
        bool blocked=false;
        for(int i=0;i<group_size[g];++i) {
            int sq=group_squares[g][i];
            blocked|=s.mort[sq]; houses+=s.level[sq];
        }
        if(blocked || houses>=4*group_size[g]) continue;
        int levels[3]={};
        for(int i=0;i<group_size[g];++i) levels[i]=s.level[group_squares[g][i]];
        double income=0;
        int cash=s.p[enemy].cash;
        for(int h=0;h<4;++h) {
            int i=std::min_element(levels,levels+group_size[g])-levels;
            int sq=group_squares[g][i],lv=levels[i]++;
            if(lv>=4 || cash<board[sq].house) break;
            cash-=board[sq].house;
            income+=landing[sq]*(board[sq].rents[lv+1]-(lv?1:2)*board[sq].rents[lv]);
        }
        best=std::max(best,income);
    }
    return best;
}
int exposure(const State& s,int p) {
    // Reserve against a likely hit on the next roll and a smaller allowance for later exposure.
    int max_rent=0, next=0;
    for(int g=0;g<Rail;++g) {
        if(s.count[1-p][g]!=group_size[g]) continue;
        for(int i=0;i<group_size[g];++i) {
            int q=group_squares[g][i];
            if(!s.level[q]) continue;
            int r=board[q].rents[s.level[q]];
            max_rent=std::max(max_rent,r);
            int gap=(q-s.p[p].pos+40)%40;
            if(gap>=2 && gap<=12) next+=(6-std::abs(gap-7))*r;
        }
    }
    return max_rent/6+next/36;
}
double earnings(const State& s,int p) {
    double result=0.;
    for(int g=0;g<Groups;++g) {
        const int n=s.count[p][g];
        if(!n) continue;
        for(int i=0;i<group_size[g];++i) {
            int q=group_squares[g][i];
            if(s.owner[q]==p && !s.mort[q]) result+=landing[q]*rent(s,q,7);
        }
    }
    return result*.001;
}

// Cost of taking money out of a deed, per dollar raised. Locked, low-yield deeds finance growth first.
double mortgage_loss(const State& s,int p,int q) {
    const int g=board[q].group;
    double lost=landing[q]*rent(s,q,7)*.001;
    if(g<Rail && s.count[p][g]==group_size[g]) lost+=14;
    if(g<Rail && s.count[1-p][g]==0) lost+=2;
    return (lost+.8)/board[q].mortgage;
}
Action best_mortgage(const State& s,int p,bool protect_sets) {
    Action result;
    double best=std::numeric_limits<double>::infinity();
    for(int g=0;g<Groups;++g) {
        if(!s.count[p][g] || (protect_sets && g<Rail && s.count[p][g]==group_size[g])) continue;
        for(int i=0;i<group_size[g];++i) {
            int q=group_squares[g][i];
            if(!can_mortgage(s,p,q)) continue;
            double loss=mortgage_loss(s,p,q);
            if(loss<best) {best=loss;result={Mortgage,q};}
        }
    }
    return result;
}
Action liquidate(const State& s,int p) {
    auto m=best_mortgage(s,p,false);
    if(m.value==Mortgage) return m;
    double best=std::numeric_limits<double>::infinity();
    Action result;
    for(int g=0;g<Rail;++g) {
        if(s.count[p][g]!=group_size[g]) continue;
        for(int i=0;i<group_size[g];++i) {
            int q=group_squares[g][i];
            if(!can_sell(s,p,q)) continue;
            int level=s.level[q];
            double loss=(board[q].rents[level]-(level==1?2:1)*board[q].rents[level-1])
                        *double(landing[q])/board[q].house;
            if(loss<best) {best=loss;result={Sell,q};}
        }
    }
    return result;
}

Action manage(const Policy& pol,const State& s,int p) {
    int cash=s.p[p].cash;
    const bool color=has_color(s,p);
    const int float_target=static_cast<int>(80+220*pol.w[Finance]);
    // During acquisition keep enough ready cash to buy a valuable unowned deed on landing.
    bool fund_deeds=pol.style==0 && cash<float_target && s.turn<65;
    if(!color && !s.mort_count[p] && !fund_deeds) return {};
    int risk=exposure(s,p);
    int reserve=static_cast<int>((25+145*pol.w[Reserve]+(.20+1.25*pol.w[Reserve])*risk)
                                *(1.10-.55*pol.w[Develop]));
    // A redeem/borrow gap covers interest and prevents the same mortgage being cycled.
    const int redeem_floor=std::max(reserve,float_target)+60;
    Action result;
    double best=0;
    bool development_needs_cash=false;
    for(int g=0;g<Rail && color;++g) {
        if(s.count[p][g]!=group_size[g]) continue;
        bool has_mort=false;
        for(int i=0;i<group_size[g];++i) has_mort|=s.mort[group_squares[g][i]];
        for(int i=0;i<group_size[g];++i) {
            int q=group_squares[g][i];
            if(has_mort) {
                if(s.mort[q] && cash-board[q].mortgage-mortgage_fee(q)>=reserve) return {Redeem,q};
                development_needs_cash=true;
                continue;
            }
            int level=s.level[q];
            if(level>=5 || (level==4?!s.hotels:!s.houses)) continue;
            if(cash-board[q].house<reserve) {
                if(level<3 && pol.w[Finance]>.15) development_needs_cash=true;
                continue;
            }
            if(!can_build(s,p,q)) continue;
            double gain=board[q].rents[level+1]-(level==0?2:1)*board[q].rents[level];
            // Early houses unlock the third-house jump. Hotels release four scarce houses to the bank.
            if(level<2) gain+=.28*(board[q].rents[3]-board[q].rents[1]);
            if(level==4 && s.houses<8 && has_color(s,1-p)) {
                if(pol.style==0 && s.houses<4
                   && landing[q]*gain<pol.w[Scarcity]*released_house_income(s,1-p)) continue;
                gain*=1.-.95*pol.w[Scarcity];
            }
            double score=landing[q]*gain/board[q].house;
            if(score>best) {best=score;result={Build,q};}
        }
    }
    if(result.value==Build) return result;
    if((development_needs_cash && pol.w[Finance]>.25) || fund_deeds) {
        if(fund_deeds && !development_needs_cash) {
            bool market=false;
            for(int g=0;g<Groups;++g) market|=s.count[0][g]+s.count[1][g]<group_size[g];
            fund_deeds=market;
        }
        if(development_needs_cash || fund_deeds) {
            auto m=best_mortgage(s,p,true);
            if(m.value==Mortgage) return m;
        }
    }
    if(!s.mort_count[p] || cash<=redeem_floor) return {};
    for(int g=0;g<Groups;++g) {
        if(!s.count[p][g]) continue;
        for(int i=0;i<group_size[g];++i) {
            int q=group_squares[g][i];
            if(s.owner[q]!=p || !s.mort[q] || cash-board[q].mortgage-mortgage_fee(q)<redeem_floor) continue;
            double score;
            // Estimate rent after redemption directly, including higher railroad/utility tiers.
            if(g==Rail) score=landing[q]*(25<<(s.count[p][Rail]-1));
            else if(g==Utility) score=landing[q]*(s.count[p][Utility]==2?70:28);
            else score=landing[q]*board[q].rents[0]*(s.count[p][g]==group_size[g]?2:1);
            score/=board[q].mortgage;
            if(score>best) {best=score;result={Redeem,q};}
        }
    }
    return result;
}

double holding_change(const Policy& pol,const State& s,int p,int q,bool gaining) {
    if(q<0) return 0.;
    const int g=board[q].group;
    int before=s.count[p][g]-(gaining?0:1);
    int opposed=s.count[1-p][g]-(gaining?1:0);
    double v=marginal(pol,s,p,q,before,opposed);
    // A purchaser assumes the mortgage principal and the bank's transfer charge.
    if(s.mort[q]) v-=board[q].mortgage+(gaining?mortgage_fee(q):0);
    return gaining?v:-v;
}
double trade_score(const Policy& pol,double my_gain,double their_gain) {
    if(my_gain<8) return -1e9;
    return my_gain-(.10+.93*pol.w[Defense])*std::max(0.,their_gain);
}
double cash_utility(double cash) {
    // Losing the last dollars can prevent an auction, a house purchase or a rent payment.
    return .55*cash+.45*std::min(cash,1800.)+.45*std::min(cash,650.)+.80*std::min(cash,250.);
}
double trade_cash(const Policy& pol,const State& s,int p,int cash_change) {
    if(pol.style!=0) return cash_change;
    return cash_utility(double(s.p[p].cash)+cash_change)-cash_utility(s.p[p].cash);
}
bool unwinds_block(const State& s,int p,int give,int take,int cash_paid) {
    if(give<0) return false;
    int g=board[give].group;
    if(g>=Rail || s.count[1-p][g]+1!=group_size[g]) return false;
    if(take>=0) {
        int tg=board[take].group;
        if(tg<Rail && s.count[p][tg]+1==group_size[tg]) return false;
    }
    return !has_color(s,p) && int64_t(s.p[1-p].cash)+cash_paid>=3*board[give].house;
}
Action offer(const Policy& pol,const State& s,int p) {
    // Reprice periodically as holdings and cash change. A deterministic schedule keeps rollouts inexpensive.
    if(s.turn<8 || ((s.turn/2)%7)!=0) return {};
    struct Deed { int q; double own, rival; };
    Deed ask_for[Groups+1]={{-1,0,0}}, give_up[Groups+1]={{-1,0,0}};
    int n_take=1,n_give=1;
    for(int g=0;g<Groups;++g) {
        for(int seat=0;seat<2;++seat) {
            int owner=seat==0?p:1-p;
            if(!s.count[owner][g] || (g<Rail && s.count[owner][g]==group_size[g])) continue;
            int q=-1;
            for(int k=0;k<group_size[g];++k) {
                int sq=group_squares[g][k];
                if(s.owner[sq]==owner && !s.mort[sq]) { q=sq;break; }
            }
            if(q<0) continue;
            bool take=owner!=p;
            Deed d{q,holding_change(pol,s,p,q,take),holding_change(pol,s,1-p,q,!take)};
            if(take) ask_for[n_take++]=d;
            else give_up[n_give++]=d;
        }
    }
    Action best_offer;
    double best=12;
    const int budget=std::max(0,s.p[p].cash-int(45+120*pol.w[Reserve]));
    const double share=.25+.18*(1.-pol.w[Demand])+.055*((s.turn/14)%4);
    for(int i=0;i<n_take;++i) for(int j=0;j<n_give;++j) {
        const auto &t=ask_for[i], &g=give_up[j];
        if(t.q<0 && g.q<0) continue;
        if(t.q>=0 && g.q>=0 && board[t.q].group==board[g.q].group) continue;
        double a=t.own+g.own,b=t.rival+g.rival;
        if(a+b<40) continue;
        int transfer=static_cast<int>(std::lround((-b+share*(a+b))/5.))*5;
        if(transfer>budget || -transfer>s.p[1-p].cash || b+transfer<8) continue;
        if(pol.style==0 && unwinds_block(s,p,g.q,t.q,transfer)) continue;
        double score=trade_score(pol,a+trade_cash(pol,s,p,-transfer),b+trade_cash(pol,s,1-p,transfer));
        if(score>best) {best=score;best_offer={1,-1,g.q,t.q,transfer};}
    }
    return best_offer;
}
Action accept(const Policy& pol,const State& s,int p,const Query& q) {
    if(q.give<0 && q.take<0) return {q.cash>0};
    if(q.give>=0 && q.take>=0 && board[q.give].group==board[q.take].group) {
        // Equal group ownership counts leave only the deed rent, price and lien differences.
        double gain=double(q.cash)+board[q.give].mortgage-board[q.take].mortgage
                    +3.*(board[q.give].rents[0]-board[q.take].rents[0]);
        if(s.mort[q.give]) gain-=board[q.give].mortgage+mortgage_fee(q.give);
        if(s.mort[q.take]) gain+=board[q.take].mortgage;
        return {gain>8};
    }
    if(pol.style==0 && unwinds_block(s,p,q.take,q.give,-q.cash)) return {};
    auto gain=[&](int who,bool recipient) {
        return holding_change(pol,s,who,q.give,recipient)
              +holding_change(pol,s,who,q.take,!recipient)+trade_cash(pol,s,who,recipient?q.cash:-q.cash);
    };
    return {trade_score(pol,gain(p,true),gain(1-p,false))>6.+20*pol.w[Demand]};
}
Action jail(const Policy& pol,const State& s,int p) {
    int unowned=28;
    for(int g=0;g<Groups;++g) unowned-=s.count[p][g]+s.count[1-p][g];
    double danger=earnings(s,1-p);
    // A roll can earn salary and first rights on a deed. Imprisonment still earns rent.
    double escape_value=17.+1.45*unowned+(1.-pol.w[JailPatience])*21.;
    if(s.p[p].cards) escape_value+=10.;
    if(danger>escape_value) {
        // On a third failure bail is mandatory. A held card saves the cash when moving is inevitable.
        if(s.p[p].jail<2 || !s.p[p].cards) return {0};
    }
    if(s.p[p].cards&1u) return {2};
    if(s.p[p].cards&2u) return {3};
    if(s.p[p].jail==2 || s.p[p].cash<50) return {0};
    if(!unowned && danger+13>escape_value) return {0};
    return {1};
}
}

double trade_deed_value(const Policy& pol,const State& s,int p,int q,bool gaining) {
    return holding_change(pol,s,p,q,gaining);
}

Action decide(const Policy& pol,const State& s,int p,const Query& q) {
    if (pol.style >= 10 && pol.style <= 12) return pressure_decide(pol,s,p,q);
    if (pol.style == 9) {
        if (pol.deal_net && (q.kind==Kind::Offer || q.kind==Kind::Accept)) return neural_trade(pol,s,p,q);
        if (pol.robust) return robust_decide(pol,s,p,q,*pol.robust);
        return adaptive_decide(pol,s,p,q);
    }
    if (pol.style == 8) return resilient_decide(pol,s,p,q);
    if (pol.style == 7) return adaptive_decide(pol, s, p, q);
    if (pol.style == 6) return neural_decide(pol, s, p, q);
    if (pol.style == 4) return holdout(s, p, q);
    if (pol.style == 5) {
        Policy base=pol;
        base.style=0;
        return q.kind == Kind::Accept ? search_accept(base,s,p,q) : decide(base,s,p,q);
    }
    if(p!=0 && p!=1) return {};
    auto deed=[](int sq) {return sq>=0 && sq<Squares && board[sq].group>=0;};
    if((q.kind==Kind::Buy || q.kind==Kind::Bid || (q.kind==Kind::Manage && q.square>=0))
        && !deed(q.square)) return {};
    if(q.kind==Kind::Accept) {
        if((q.give!=-1 && (!deed(q.give) || s.owner[q.give]!=1-p)) ||
           (q.take!=-1 && (!deed(q.take) || s.owner[q.take]!=p)) ||
           int64_t(s.p[p].cash)+q.cash<0 || int64_t(s.p[1-p].cash)-q.cash<0) return {};
    }
    switch(q.kind) {
    case Kind::Buy: {
        auto limit=price_limit(pol,s,p,q.square);
        // A bidder can force a cheaper auction when its opponent cannot reach face price.
        if(pol.style==0 && s.p[1-p].cash<board[q.square].price-1) return {};
        return {limit>=board[q.square].price};
    }
    case Kind::Bid:
        if(q.amount<0) {
            const auto& d=board[q.square];
            int lv=s.level[q.square];
            if(lv<0 || lv>=5) return {};
            double improvement=d.rents[lv+1]-(lv?1:2)*d.rents[lv];
            double price=d.house+improvement*(.40+1.3*pol.w[Scarcity]);
            return {std::clamp(int(price),0,s.p[p].cash)};
        }
        return {price_limit(pol,s,p,q.square)};
    case Kind::Manage:
        if(q.square>=0) {
            int debt=board[q.square].mortgage+mortgage_fee(q.square);
            return s.p[p].cash>=debt+int(75+160*pol.w[Reserve])?Action{Redeem,q.square}:Action{};
        }
        return manage(pol,s,p);
    case Kind::Liquidate:return liquidate(s,p);
    case Kind::Jail:return jail(pol,s,p);
    case Kind::Offer:return offer(pol,s,p);
    case Kind::Accept:return accept(pol,s,p,q);
    }
    return {};
}
} // namespace mono
