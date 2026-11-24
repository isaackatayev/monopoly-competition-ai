#include "bid_trace.hpp"
#include "groups.hpp"
#include <limits>

namespace mono {
namespace {

// A fixed two-player competitor. It keeps blocking deeds and declines all trades.
int remaining_deeds(const State& s) {
    int remaining=28;
    for(int g=0;g<Groups;++g) remaining-=s.count[0][g]+s.count[1][g];
    return remaining;
}

int dice_ways(int from,int to) {
    int distance=(to-from+Squares)%Squares;
    if(distance<2 || distance>12) return 0;
    return distance<=7 ? distance-1 : 13-distance;
}

int cash_floor(const State& s,int who) {
    int expected=0;
    for(int steps=2;steps<=12;++steps) {
        int target=(s.p[who].pos+steps)%Squares;
        if(s.owner[target]==1-who)
            expected+=dice_ways(s.p[who].pos,target)*rent(s,target,steps);
    }
    return 60+expected/24;
}

int max_price(const State& s,int who,int sq) {
    const Space& d=board[sq];
    const int group=d.group, owned=s.count[who][group], rival=s.count[1-who][group];
    int value=d.price, reserve=cash_floor(s,who);
    if(group<Rail) {
        const bool completes=owned+1==group_size[group], blocks=rival+1==group_size[group];
        // Price a completed group against its third-house rents and development cost.
        int rents=0;
        for(int i=0;i<group_size[group];++i) rents+=board[group_squares[group][i]].rents[3];
        if(completes || blocks) {
            int surplus=std::max(0,rents-group_size[group]*d.house);
            value+=completes ? surplus*2/3 : surplus/2;
            reserve/=3;
        } else if(!rival) value+=owned ? d.price*2/3 : 90;
        else if(!owned) value+=65;
        else value=d.price*3/4;
    } else if(group==Rail) {
        value=210+owned*105+(rival>=2 ? 110 : 0);
    } else {
        value=owned ? 200 : 120;
    }
    return std::clamp(value,0,std::max(0,s.p[who].cash-reserve));
}

// Prefer to keep active railroad income and intact groups when raising cash.
Action borrow(const State& s,int who,bool forced) {
    double best=std::numeric_limits<double>::infinity();
    Action action;
    for(int sq=0;sq<Squares;++sq) {
        if(s.owner[sq]!=who || !can_mortgage(s,who,sq)) continue;
        const Space& d=board[sq];
        const int g=d.group;
        bool complete=g<Rail && s.count[who][g]==group_size[g];
        if(!forced && (complete || (g==Rail && s.count[who][g]>=2))) continue;
        double cost=double(rent(s,sq,7))/d.mortgage;
        if(complete) cost+=4.;
        if(cost<best) {best=cost;action={Mortgage,sq};}
    }
    return action;
}

Action manage_assets(const State& s,int who) {
    const int cash=s.p[who].cash, floor=cash_floor(s,who);
    Action next;
    double best=0;
    bool can_finance=false;
    for(int g=0;g<Rail;++g) {
        if(s.count[who][g]!=group_size[g]) continue;
        for(int i=0;i<group_size[g];++i) {
            const int sq=group_squares[g][i], lv=s.level[sq];
            const Space& d=board[sq];
            if(s.mort[sq]) {
                if(cash-d.mortgage-(d.mortgage+9)/10>=floor && can_redeem(s,who,sq))
                    return {Redeem,sq};
                can_finance=true;
                continue;
            }
            if(lv>=5 || (lv==4 ? !s.hotels : !s.houses)) continue;
            if(lv==4 && s.houses<=2) {
                bool rival_group=false;
                for(int enemy=0;enemy<Rail;++enemy)
                    rival_group|=s.count[1-who][enemy]==group_size[enemy];
                if(rival_group) continue;
            }
            if(cash-d.house<floor) {
                if(lv<3) can_finance=true;
                continue;
            }
            if(!can_build(s,who,sq)) continue;
            int immediate=d.rents[lv+1]-(lv ? 1 : 2)*d.rents[lv];
            // Include the benefit of being one investment closer to three houses.
            int program=lv<3 ? (d.rents[3]-d.rents[lv])/(3-lv) : immediate;
            int visits=12+dice_ways(s.p[1-who].pos,sq);
            double score=double(visits)*(immediate+program)/d.house;
            if(score>best) {best=score;next={Build,sq};}
        }
    }
    if(next.value==Build) return next;
    const int remaining=remaining_deeds(s);
    int float_target=remaining>5 ? 250 : 120;
    if(can_finance || (remaining && cash<float_target)) {
        auto action=borrow(s,who,false);
        if(action.value==Mortgage) return action;
    }
    if(!s.mort_count[who]) return {};
    for(int sq=0;sq<Squares;++sq) {
        if(!s.mort[sq] || !can_redeem(s,who,sq)) continue;
        const Space& d=board[sq];
        if(cash-d.mortgage-(d.mortgage+9)/10>std::max(floor,float_target)+110)
            return {Redeem,sq};
    }
    return {};
}

Action cover_debt(const State& s,int who) {
    Action loan=borrow(s,who,true);
    if(loan.value==Mortgage) return loan;
    double best=std::numeric_limits<double>::infinity();
    Action next;
    for(int sq=0;sq<Squares;++sq) {
        if(!can_sell(s,who,sq)) continue;
        const Space& d=board[sq];
        int lv=s.level[sq];
        double loss=double(d.rents[lv]-(lv==1 ? 2 : 1)*d.rents[lv-1])/d.house;
        if(lv==5 && s.houses<4) loss+=10.;
        if(loss<best) {best=loss;next={Sell,sq};}
    }
    return next;
}

Action jail_choice(const State& s,int who) {
    int total_rent=0;
    for(int sq=0;sq<Squares;++sq)
        if(s.owner[sq]==1-who) total_rent+=rent(s,sq,7);
    const bool seek_deeds=remaining_deeds(s)>=4;
    if(total_rent>40*(seek_deeds ? 52 : 28) && s.p[who].jail<2) return {};
    if(s.p[who].cards&1u) return {2};
    if(s.p[who].cards&2u) return {3};
    return {seek_deeds && s.p[who].jail<2 && s.p[who].cash>=85};
}
}

Action holdout(const State& s,int who,const Query& q) {
    switch(q.kind) {
    case Kind::Buy:
        return {max_price(s,who,q.square)>=board[q.square].price};
    case Kind::Bid:
        if(q.amount<0) {
            const int lv=s.level[q.square];
            if(lv>=5) return {};
            const Space& d=board[q.square];
            int premium=(d.rents[lv+1]-d.rents[lv])/2;
            explain_bid("Building cost plus half the marginal rent increase, limited by cash.",
                        {{"cost",double(d.house)},{"rent_premium",double(premium)},{"cash",double(s.p[who].cash)}});
            return {std::min(s.p[who].cash,d.house+premium)};
        }
        return {max_price(s,who,q.square)};
    case Kind::Manage:
        if(q.square>=0) {
            if(can_redeem(s,who,q.square) && s.p[who].cash-q.amount>=cash_floor(s,who))
                return {Redeem,q.square};
            return {};
        }
        return manage_assets(s,who);
    case Kind::Liquidate: return cover_debt(s,who);
    case Kind::Jail: return jail_choice(s,who);
    case Kind::Offer:
    case Kind::Accept: return {};
    }
    return {};
}
} // namespace mono
