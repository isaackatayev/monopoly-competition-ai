#include "bid_trace.hpp"
#include "game.hpp"
#include "groups.hpp"
#include "neural_value.hpp"
#include "neural_trade.hpp"
#include <cmath>
#include <limits>

namespace mono {
namespace {
int fee(int sq) {return (board[sq].mortgage+9)/10;}
bool deed(int sq) {return sq>=0 && sq<Squares && board[sq].group>=0;}
bool same_move(Action a,Action b) {return a.value==b.value && a.square==b.square;}

void purchase(State& s,int who,int sq,int price) {
    s.owner[sq]=who;
    ++s.count[who][board[sq].group];
    s.p[who].cash-=price;
}
void improve(State& s,int who,int sq,int price) {
    ++s.level[sq];
    if(s.level[sq]==5) {--s.hotels;s.houses+=4;}
    else --s.houses;
    s.p[who].cash-=price;
}
// Copy the immediate economic effects; the engine independently validates and applies the chosen move.
void transact(State& s,int who,Action action) {
    const int sq=action.square;
    if(action.value==Build) {improve(s,who,sq,board[sq].house);return;}
    if(action.value==Mortgage) {
        s.mort[sq]=true;++s.mort_count[who];s.p[who].cash+=board[sq].mortgage;return;
    }
    if(action.value==Redeem) {
        s.mort[sq]=false;--s.mort_count[who];s.p[who].cash-=board[sq].mortgage+fee(sq);return;
    }
    if(action.value!=Sell) return;
    int g=board[sq].group;
    if(s.level[sq]==5 && s.houses<4) {
        for(int i=0;i<group_size[g];++i) {
            int t=group_squares[g][i],lv=s.level[t];
            s.p[who].cash+=lv*(board[t].house/2);
            if(lv==5) ++s.hotels; else s.houses+=lv;
            s.level[t]=0;
        }
    } else {
        if(s.level[sq]--==5) {++s.hotels;s.houses-=4;} else ++s.houses;
        s.p[who].cash+=board[sq].house/2;
    }
}

struct Advisor {
    const NeuralNet& net;
    Policy prior;
    int who;

    float value(const State& s) const {return neural_value(net,s,who);}

    // At the marginal auction price, decide whether acquiring the deed is better than
    // forcing the other player to pay for it. Both branches include the cash actually spent.
    float auction_edge(const State& s,int sq,int price) const {
        State win=s,lose=s;
        purchase(win,who,sq,price);
        purchase(lose,1-who,sq,std::min(price,s.p[1-who].cash));
        return value(win)-value(lose);
    }
    int price_limit(const State& s,int sq) const {
        int cash=s.p[who].cash;
        if(cash<10) { explain_bid("Cash below auction minimum.", {{"cash",double(cash)}}); return 0; }
        int anchor=decide(prior,s,who,{Kind::Bid,sq,10}).value;
        int ceiling=std::min(cash,int(1.7*anchor+100));
        if(ceiling<10) return 0;
        // Do not relinquish critical blocking rights on a small predicted value edge.
        int low=std::min(ceiling,int(.98f*anchor)),high=ceiling+1;
        // The learned reservation price regularizes noisy counterfactuals at extreme bids.
        for(int i=0;i<5 && high-low>1;++i) {
            int price=std::clamp(i==0?anchor:(low+high)/2,low+1,high-1);
            float advantage=auction_edge(s,sq,price)+(anchor-price)*.00050f;
            if(advantage>=0) low=price; else high=price;
        }
        explain_bid("Searched for the highest tested price favoring purchase over rival ownership, with a floor tied to the prior valuation.",
                    {{"prior_ceiling",double(anchor)},{"search_floor",double(std::min(ceiling,int(.98f*anchor)))},
                     {"search_cap",double(ceiling)},{"final_lower",double(low)},{"final_upper",double(high)}});
        return low;
    }
    Action buy(const State& s,int sq) const {
        const int sticker=board[sq].price,rival=1-who;
        if(s.p[who].cash<sticker || s.p[rival].cash<sticker-1) return {};
        const int limit=price_limit(s,sq);
        State direct=s;
        purchase(direct,who,sq,sticker);
        const float direct_value=value(direct);
        // Infer a rival's auction demand from observable holdings and a population
        // of fixed bidders, then compare buying immediately with running the auction.
        float auction_value=0,worst=1;
        for(const Policy& style:{preset(1),preset(2),preset(3),prior}) {
            int rival_bid=decide(style,s,rival,{Kind::Bid,sq,10}).value;
            State ending=s;
            if(limit==rival_bid && limit>=10) {
                State tie=s;
                purchase(ending,who,sq,limit);
                purchase(tie,rival,sq,limit);
                float tied_value=(value(ending)+value(tie))*.5f;
                auction_value+=tied_value;
                worst=std::min(worst,tied_value);
                continue;
            }
            if(limit>rival_bid && limit>=10) purchase(ending,who,sq,std::max(10,rival_bid+1));
            else if(rival_bid>=10) purchase(ending,rival,sq,std::max(10,limit+1));
            float ending_value=value(ending);
            auction_value+=ending_value;
            worst=std::min(worst,ending_value);
        }
        const bool original=decide(prior,s,who,{Kind::Buy,sq,sticker}).value!=0;
        // Mix the population mean with the least favorable rival. Owning the
        // landing grants a purchase option that is lost as soon as bidding opens.
        float forecast=.5f*(auction_value*.25f+worst);
        return {direct_value-forecast+(original?.010f:-.010f)>0};
    }

    Action manage(const State& s,Action anchor) const {
        const int cash=s.p[who].cash;
        // Borrowing and redemption occupy disjoint liquidity regions. An agent cannot
        // improve its score by repeatedly flipping a mortgage inside one decision window.
        int remaining=28;
        for(int g=0;g<Groups;++g) remaining-=s.count[0][g]+s.count[1][g];
        bool needs_build_funds=false;
        for(int g=0;g<Rail;++g) {
            if(s.count[who][g]!=group_size[g]) continue;
            int first=group_squares[g][0];
            if(s.level[first]<3 && s.houses && cash<board[first].house+120) needs_build_funds=true;
        }
        const bool seek_loan=anchor.value==Mortgage || needs_build_funds || (remaining>2 && cash<140);
        const bool redeem=anchor.value==Redeem || cash>340;
        std::array<Action,2*Squares> candidates{};
        int n=0;
        if(anchor.value!=Pass) candidates[n++]=anchor;
        for(int g=0;g<Groups;++g) {
            if(!s.count[who][g]) continue;
            bool complete=g<Rail && s.count[who][g]==group_size[g];
            if(!complete && !seek_loan && !(redeem && s.mort_count[who])) continue;
            for(int i=0;i<group_size[g];++i) {
                int sq=group_squares[g][i];
                if(s.owner[sq]!=who) continue;
                Action a;
                if(complete && can_build(s,who,sq) && cash-board[sq].house>=30) {
                    // Keep a house shortage intact unless the regular policy agrees to release it.
                    if(s.level[sq]<4 || s.houses>=4 || anchor.value==Build) a={Build,sq};
                } else if(redeem && s.mort[sq] && can_redeem(s,who,sq) &&
                          cash-board[sq].mortgage-fee(sq)>(complete?110:280)) a={Redeem,sq};
                else if(seek_loan && !complete && can_mortgage(s,who,sq)) a={Mortgage,sq};
                if(a.value!=Pass && !same_move(a,anchor)) candidates[n++]=a;
            }
        }
        if(!n) return {};
        Action best;
        float best_score=value(s)+(anchor.value==Pass?.012f:0.f);
        for(int j=0;j<n;++j) {
            Action a=candidates[j];
            State next=s;
            transact(next,who,a);
            float score=value(next);
            if(a.value==Mortgage && needs_build_funds) {
                auto follow=decide(prior,next,who,{Kind::Manage});
                if(follow.value==Build && can_build(next,who,follow.square)) {
                    transact(next,who,follow);
                    score=std::max(score,value(next)-.003f);
                }
            }
            if(a.value==Build) score+=.004f;
            if(same_move(a,anchor)) score+=.018f;
            if(score>best_score) {best=a;best_score=score;}
        }
        return best;
    }

    Action liquidate(const State& s,const Query& q,Action anchor) const {
        Action best=anchor;
        float best_score=-std::numeric_limits<float>::infinity();
        for(int sq=0;sq<Squares;++sq) {
            if(s.owner[sq]!=who) continue;
            Action a;
            if(can_mortgage(s,who,sq)) a={Mortgage,sq};
            else if(can_sell(s,who,sq)) a={Sell,sq};
            else continue;
            State next=s;
            transact(next,who,a);
            int shortfall=std::max(0,q.amount-next.p[who].cash);
            next.p[who].cash=std::max(0,next.p[who].cash-q.amount);
            float score=value(next)-std::min(1200,shortfall)*.00010f;
            if(same_move(a,anchor)) score+=.010f;
            if(score>best_score) {best_score=score;best=a;}
        }
        return best;
    }

    bool forecast_payment(State& next,int creditor,int amount) const {
        // Resolve a known rent or bail bill before valuing the resulting board.
        auto& cash=next.p[who].cash;
        while(cash<amount) {
            auto action=decide(prior,next,who,{Kind::Liquidate,-1,amount});
            if((action.value!=Sell || !can_sell(next,who,action.square)) &&
               (action.value!=Mortgage || !can_mortgage(next,who,action.square))) return false;
            transact(next,who,action);
        }
        cash-=amount;
        if(creditor>=0) next.p[creditor].cash+=amount;
        return true;
    }
    float after_jail_move(State next,int sum,bool extra_roll=false) const {
        const int rival=1-who;
        auto& p=next.p[who];
        p.jail=-1;
        p.pos=10+sum;
        next.active=extra_roll?who:rival;
        if(next.owner[p.pos]==rival) {
            // A C1009 utility landing uses a separate rent roll; seven is its mean.
            if(!forecast_payment(next,rival,rent(next,p.pos,7))) return 0;
        } else if(deed(p.pos) && next.owner[p.pos]<0 && p.cash>=board[p.pos].price &&
                  decide(prior,next,who,{Kind::Buy,p.pos,board[p.pos].price}).value) {
            purchase(next,who,p.pos,board[p.pos].price);
        }
        // A card square is valued at arrival before the unseen draw.
        return value(next);
    }
    float jail_value(const State& s,int choice) const {
        State next=s;
        if(choice==1) next.p[who].cash-=50;
        if(choice>=2) next.p[who].cards&=~(1u<<(choice-2));
        bool waiting=choice==0,third=s.p[who].jail==2;
        float expected=0;
        if(waiting && !third) {
            State stay=s;
            ++stay.p[who].jail; stay.active=1-who;
            expected=30*value(stay);
        }
        for(int sum=2;sum<=12;++sum) {
            const int ways=6-std::abs(7-sum),doubles=(sum%2)==0;
            if(!waiting) {
                if(doubles) expected+=after_jail_move(next,sum,true);
                if(ways>doubles) expected+=(ways-doubles)*after_jail_move(next,sum);
            }
            else {
                if(doubles) expected+=after_jail_move(next,sum);
                if(third && ways>doubles) {
                    State forced=next;
                    if(forecast_payment(forced,-1,50))
                        expected+=(ways-doubles)*after_jail_move(forced,sum);
                }
            }
        }
        return expected/36;
    }
    Action jail(const State& s,Action anchor) const {
        if(s.p[who].jail<0) return anchor;
        float best=-1;
        Action result;
        for(int choice=0;choice<4;++choice) {
            if(choice==1 && s.p[who].cash<50) continue;
            if(choice>=2 && !(s.p[who].cards&(1u<<(choice-2)))) continue;
            float score=jail_value(s,choice)+(choice==anchor.value?.007f:0.f);
            if(score>best) {best=score;result={choice};}
        }
        return result;
    }
};
}

Action neural_decide(const Policy& pol,const State& s,int who,const Query& q) {
    Policy base=pol; base.style=0; base.external=nullptr;base.context=nullptr;
    if(!pol.net || who<0 || who>1) return decide(base,s,who,q);
    if(q.kind==Kind::Offer || q.kind==Kind::Accept) return neural_trade(pol,s,who,q);
    Advisor advisor{*pol.net,base,who};
    Action anchor=decide(base,s,who,q);
    if(q.kind==Kind::Buy) return deed(q.square) && s.owner[q.square]<0?advisor.buy(s,q.square):anchor;
    if(q.kind==Kind::Bid) {
        if(!deed(q.square)) return {};
        if(q.amount>=0) return s.owner[q.square]<0?Action{advisor.price_limit(s,q.square)}:anchor;
        int sq=q.square;
        if(!can_build(s,who,sq)) return anchor;
        // A rationed building auction has a different opportunity cost from a deed auction.
        const int ceiling=std::min(s.p[who].cash,anchor.value*2+50);
        int low=0,high=ceiling+1;
        float pass=advisor.value(s);
        for(int iter=0;iter<5 && high-low>1;++iter) {
            int price=(low+high)/2;
            State win=s;improve(win,who,sq,price);
            if(advisor.value(win)-pass+(anchor.value-price)*.00050f>=0) low=price; else high=price;
        }
        explain_bid("Compared building now with passing at candidate prices, adjusted toward the prior building valuation.",
                    {{"prior_ceiling",double(anchor.value)},{"search_cap",double(ceiling)},{"final_lower",double(low)},{"final_upper",double(high)}});
        return {low};
    }
    if(q.kind==Kind::Jail) return advisor.jail(s,anchor);
    if(q.kind==Kind::Liquidate) return advisor.liquidate(s,q,anchor);
    if(q.kind==Kind::Manage && q.square<0) return advisor.manage(s,anchor);
    if(q.kind==Kind::Manage && can_redeem(s,who,q.square)) {
        State redeem=s,keep=s;
        transact(redeem,who,{Redeem,q.square});keep.p[who].cash-=fee(q.square);
        if(advisor.value(redeem)-advisor.value(keep)+(anchor.value==Redeem?.008f:-.008f)>0)
            return {Redeem,q.square};
        return {};
    }
    return anchor;
}
} // namespace mono
