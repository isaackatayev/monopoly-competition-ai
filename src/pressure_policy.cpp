#include "bid_trace.hpp"
#include "pressure_policy.hpp"
#include "neural_trade.hpp"
#include "groups.hpp"
#include <cmath>
namespace mono {
const std::array<const char*,PressureParams> pressure_names={
    "bid_multiplier","denial_multiplier","cash_reserve","asset_manager","offer_mode",
    "preservation_gate","blocker_mix","railroad_multiplier","completion_multiplier",
    "pressure_threshold","rival_cash_weight","prior_pressure"
};
namespace {
enum Gene {BidMultiplier,DenialMultiplier,CashReserve,AssetManager,OfferMode,PreservationGate,
           BlockerMix,RailroadMultiplier,CompletionMultiplier,PressureThreshold,RivalCashWeight,PriorPressure};
bool talked(const State& s,int enemy){
    const auto& h=s.behavior[enemy];
    for(int i=0;i<h.size;++i){int k=h.events[i].kind;
        if(k==ProposedTrade || k==AcceptedTrade || k==ProposedBundle || k==AcceptedBundle)return true;}
    return false;
}
bool contradiction(const State& s,int who,const Query& q) {
    const int enemy=1-who;
    if(talked(s,enemy))return true;
    const auto& h=s.behavior[enemy];
    if(q.kind==Kind::Bid && q.amount>=0 && h.size) {
        const auto& e=h.events[h.size-1];
        if(e.kind==DeclinedFace && e.turn==s.turn && e.square==q.square &&
                holdout(s,enemy,{Kind::Buy,q.square,board[q.square].price}).value)return true;
    }
    for(int p=0;p<2;++p){
        const auto& events=s.behavior[p];
        for(int i=events.size-1;i>=0;--i) {
            const auto& e=events.events[i];int sq=e.square;
            if(sq<0 || sq>=Squares || board[sq].group<0)continue;
            int g=board[sq].group;
            if((e.kind!=WonAuction && (p==who || e.kind!=BoughtAtFace)))continue;
            int mine=s.count[enemy][g]-(p==enemy),theirs=s.count[who][g]-(p==who);
            if(mine<0 || theirs<0)continue;
            if(g<Rail && (mine+1>=group_size[g] || theirs+1>=group_size[g]))continue;
            int high=g==Utility?(mine?200:120):g==Rail?210+105*mine+(theirs>=2?110:0):
                         std::max(board[sq].price+90,board[sq].price*(mine?5:3)/3);
            if(e.amount>high+int(p==who))return true;
        }
    }
    return false;
}
// Infer an overall bidding premium from paid clearing prices. The losing seat's
// proxy is visible to within one dollar only when the other player won. Winners'
// paid prices provide lower bounds and cash constraints can censor observations.
double market_pressure(const Policy& base,const State& s,int who,const std::array<double,12>& w) {
    const int enemy=1-who;
    const double threshold=w[PressureThreshold];
    double evidence=(w[PriorPressure]-.5)*2.5;
    int last_trade=-1;
    for(const auto& h:s.behavior)for(int i=0;i<h.size;++i) {
        const auto& e=h.events[i];
        if(e.kind==AcceptedTrade || e.kind==AcceptedBundle)last_trade=std::max(last_trade,e.turn);
    }
    Policy neural=base;neural.style=6;neural.external=nullptr;neural.deal_net=nullptr;
    for(int buyer=0;buyer<2;++buyer) {
        const auto& h=s.behavior[buyer];
        int used=0;
        for(int i=h.size-1;i>=0 && used<3;--i) {
            const auto& e=h.events[i];
            if(e.turn<=last_trade)break;
            if(e.kind!=WonAuction || e.amount<55 || e.square<0 || e.square>=Squares)continue;
            State before=s;
            for(int p=0;p<2;++p){
                const auto& history=s.behavior[p];int restores=0;
                for(int j=history.size-1;j>=0 && history.events[j].turn>=e.turn;--j) {
                    const auto& sale=history.events[j];int sq=sale.square;
                    if((sale.kind!=WonAuction && sale.kind!=BoughtAtFace) || sq<0 || sq>=Squares ||
                       board[sq].group<0 || before.owner[sq]!=p)continue;
                    restores+=sale.amount;
                    before.owner[sq]=-1;--before.count[p][board[sq].group];
                    if(before.level[sq]==5)++before.hotels;else before.houses+=before.level[sq];
                    before.level[sq]=0;before.mort_count[p]-=before.mort[sq];before.mort[sq]=false;
                }
                before.p[p].cash+=restores;
            }
            const int sq=e.square,g=board[sq].group;
            if(g<0 || before.owner[sq]>=0)continue;
            int cash=before.p[enemy].cash,paid=e.amount-int(buyer==who);
            before.turn=e.turn;
            before.p[enemy].cash=std::max({cash,paid+40,550});
            int fair=decide(neural,before,enemy,{Kind::Bid,sq,10}).value;
            if(fair<50)continue;
            double relative=double(paid)/fair;
            if(before.count[who][g]+1==group_size[g])relative/=1.10;
            // A high-price win is a lower bound; a low-price win says little.
            double signal=std::clamp((relative-threshold)*4.,-1.,1.);
            if(buyer==enemy && signal<0)continue;
            if(buyer==who && signal<0 && std::abs(paid-cash)<std::max(35,int(.10*cash)))continue;
            double recency=1./(1.+.05*std::max(0,s.turn-e.turn));
            evidence+=recency*(buyer==who ? 1. : .62)*signal;
            ++used;
        }
    }
    if(s.p[enemy].cash<110)evidence+=w[RivalCashWeight]*(1.-s.p[enemy].cash/110.);
    return evidence;
}

}
Action pressure_decide(const Policy& policy,const State& s,int who,const Query& q) {
    if(who<0 || who>1)return {};
    if(policy.style==12) {
        if(q.kind==Kind::Offer) return neural_trade(policy,s,who,q);
        Policy original=policy;
        original.style=11;
        Action action=pressure_decide(original,s,who,q);
        if((q.kind!=Kind::Bid && q.kind!=Kind::Buy) || q.amount<0 ||
           q.square<0 || q.square>=Squares || s.owner[q.square]>=0) return action;
        const auto& deed=board[q.square];
        if(deed.group<0) return action;
        // The floor uses cash already on hand, leaving the cost of jail bail.
        const int available=std::max(0,s.p[who].cash-50);
        const bool blocks=deed.group<Rail && s.count[1-who][deed.group]+1==group_size[deed.group];
        int floor=deed.mortgage;
        if(blocks) floor=deed.price+deed.house;
        floor=std::min(floor,available);
        if(q.kind==Kind::Buy) {
            if(blocks && floor>=deed.price) action.value=1;
        } else if(floor>=10 && action.value<floor) {
            explain_bid(blocks ? "Raised ceiling to block the rival's last street, limited by spendable cash."
                               : "Raised ceiling to the deed's mortgage value, limited by spendable cash.",
                        {{"original_bid",double(action.value)},{"mortgage",double(deed.mortgage)},
                         {"face_price",double(deed.price)},{"building_cost",double(deed.house)},
                         {"cash_after_bail_reserve",double(available)},{"adjusted_bid",double(floor)}});
            action.value=floor;
        }
        return action;
    }

    Policy base=policy;
    base.style=9;
    base.external=nullptr;
    if(!policy.pressure)return decide(base,s,who,q);
    if((q.kind==Kind::Buy || q.kind==Kind::Bid) &&
       (q.square<0 || q.square>=Squares || board[q.square].group<0 ||
        ((q.kind==Kind::Buy || q.amount>=0) && s.owner[q.square]>=0)))return {};
    const auto& w=policy.pressure->w;
    if(w[OfferMode]>=1.5 && q.kind==Kind::Offer)return {};
    if(q.kind==Kind::Offer || q.kind==Kind::Accept)return decide(base,s,who,q);
    const bool acquisition=q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0);
    const bool asset=q.kind==Kind::Manage || q.kind==Kind::Liquidate || q.kind==Kind::Jail || q.kind==Kind::Bid;
    if(!acquisition && (!asset || w[AssetManager]<.5))return decide(base,s,who,q);
    if(w[PreservationGate]>.5 && !contradiction(s,who,q))return decide(base,s,who,q);
    if(policy.style==11 && market_pressure(base,s,who,w)<=0)return decide(base,s,who,q);
    Policy neural=base;neural.style=6;neural.external=nullptr;neural.deal_net=nullptr;
    if(!acquisition){
        if(w[AssetManager]<1.5)return decide(neural,s,who,q);
        return holdout(s,who,q);
    }
    const int sq=q.square, g=board[sq].group,other=1-who,face=board[sq].price;
    const auto lookup=Query{Kind::Bid,sq,10};
    int neural_bid=decide(neural,s,who,lookup).value;
    const double equity=(1.-w[BlockerMix])*neural_bid+w[BlockerMix]*holdout(s,who,lookup).value;
    double markup=w[BidMultiplier];
    if(s.count[other][g]+1==group_size[g])markup*=w[DenialMultiplier];
    if(g==Rail)markup*=w[RailroadMultiplier];
    if(g<Rail && s.count[who][g]+1==group_size[g])markup*=w[CompletionMultiplier];
    int cap=std::max(0,s.p[who].cash-int(w[CashReserve]));
    int maxbid=std::clamp(int(equity*markup),0,cap);
    if(q.kind==Kind::Bid) {
        explain_bid("Blended deed valuations, applied acquisition multipliers, then capped the ceiling at spendable cash.",
                    {{"neural_ceiling",double(neural_bid)},{"blended_value",equity},{"multiplier",markup},
                     {"uncapped_ceiling",double(int(equity*markup))},{"cash_reserve",double(int(w[CashReserve]))},{"cash_cap",double(cap)}});
        return {maxbid};
    }
    return {maxbid>=face && s.p[other].cash+1>=face};
}
}
