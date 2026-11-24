#include "bid_trace.hpp"
#include "robust_policy.hpp"
#include "adaptive_policy.hpp"
#include "groups.hpp"
#include "neural_value.hpp"
#include <cmath>

namespace mono {
const std::array<const char*,RobustParams> robust_names={
 "previous_rival_mass","recent_rival_mass","additive_mass","open_population_mass",
 "deed_income","rival_spending","cash_stress","first_purchase_right","working_cash",
 "housing_priority","value_network_mix","worst_outcome_weight"
};
namespace {
enum Gene {FirstRival,SecondRival,Additive,OpenPopulation,DeedIncome,RivalSpend,CashStress,
           PurchaseRight,WorkingCash,HousingPriority,NetworkMix,Worst};
using Genes=std::array<double,RobustParams>;
struct Forecast {int price;double mass;};
struct Recipe {double mix,markup,denial,reserve;};
// Publicly disclosed parameters of the two earlier qualification challengers. They are
// two hypotheses in the price distribution, never identifiers of a current rival.
constexpr Recipe sparring[]={
 {.7188329387795263,1.2108807282093618,.9162143258160599,240*.0398552818993404},
 {1.,1.1718174614742098,1.133344448810712,240*.014910537113815}
};
int price(const State& s,int sq,int p,int block,int neural,Recipe r) {
    const int g=board[sq].group;
    double multiplier=r.markup;
    if(s.count[1-p][g]+1==group_size[g]) multiplier*=r.denial;
    int limit=std::max(0,s.p[p].cash-int(r.reserve));
    return std::clamp(int(((1.-r.mix)*block+r.mix*neural)*multiplier),0,limit);
}
struct Likelihood {
    double first=1,second=1,additive=1,unknown=1;
    bool legacy_fit=false;
};
Likelihood price_evidence(const Policy& policy,const State& current,int who,int mode) {
    Likelihood result;
    if(!mode || !policy.net) return result;
    int rival=1-who;
    Policy base=policy;base.style=0;base.external=nullptr;
    Policy neural=policy;neural.style=6;neural.external=nullptr;
    if(mode!=3) {
        // Asset choices are public. A rival that just managed is more likely to
        // follow a policy that has no remaining transaction than one asking to buy,
        // mortgage or redeem immediately. Treat this as soft evidence only.
        auto hard=holdout(current,rival,{Kind::Manage});
        auto learned=neural_decide(neural,current,rival,{Kind::Manage});
        if(hard.value!=Pass && learned.value==Pass) {result.first*=.57;result.second*=2.1;}
        if(hard.value==Pass && learned.value!=Pass) {result.first*=2.1;result.second*=.57;}
    }
    if(mode==2) return result;
    int last_trade=-1;
    for(const auto& h:current.behavior) for(int i=0;i<h.size;++i) {
        const auto& e=h.events[i];
        if(e.kind==AcceptedTrade || e.kind==AcceptedBundle)last_trade=std::max(last_trade,e.turn);
    }
    const auto& sales=current.behavior[who];
    int used=0;
    for(int i=sales.size-1;i>=0 && used<3;--i) {
        const auto& e=sales.events[i];
        if(e.turn<=last_trade)break;
        if(e.kind!=WonAuction || e.amount<45 || e.square<0 || e.square>=Squares || board[e.square].group<0)continue;
        State before=current;
        // Reverse reported deed acquisitions. Old cash and positions are unobserved;
        // allow enough cash for the observed losing bid and discount the residual error.
        for(int p=0;p<2;++p) {
            const auto& h=current.behavior[p];
            for(int j=h.size-1;j>=0 && h.events[j].turn>=e.turn;--j) {
                const auto& later=h.events[j];int sq=later.square;
                if((later.kind!=WonAuction && later.kind!=BoughtAtFace) || sq<0 || sq>=Squares ||
                    before.owner[sq]!=p || board[sq].group<0)continue;
                before.owner[sq]=-1;--before.count[p][board[sq].group];
                if(before.level[sq]==5)++before.hotels;else before.houses+=before.level[sq];
                before.level[sq]=0;before.mort_count[p]-=before.mort[sq];before.mort[sq]=false;
            }
        }
        int sq=e.square,g=board[sq].group;
        if(before.owner[sq]>=0)continue;
        before.turn=e.turn;before.p[rival].cash=std::max(1800,e.amount+180);
        before.p[who].cash=std::max(550,current.p[who].cash);
        int b=holdout(before,rival,{Kind::Bid,sq,10}).value;
        int n=neural_decide(neural,before,rival,{Kind::Bid,sq,10}).value;
        int fast=decide(base,before,rival,{Kind::Bid,sq,10}).value;
        int x=e.amount-1;
        double p1=price(before,sq,rival,b,n,sparring[0]), p2=price(before,sq,rival,b,n,sparring[1]);
        double pa=.6*n+.4*board[sq].price+35+42*before.count[rival][g];
        if(g==Rail)pa+=100;
        if(g<Rail && before.count[who][g]+1==group_size[g])pa+=330;
        if(g<Rail && before.count[rival][g]+1==group_size[g])pa+=505;
        if(x<.70*std::min({p1,p2,pa}))continue; // Likely censored by unavailable cash.
        int mdl[3]={int(p1),int(p2),int(pa)};
        if(std::abs(x-fast)<=2 && std::abs(x-mdl[0])>18 && std::abs(x-mdl[1])>18)result.legacy_fit=true;
        double l[3];
        for(int k=0;k<3;++k)l[k]=std::min(2.8,std::abs(x-mdl[k])/(16.+.080*std::max(x,mdl[k])));
        double lowest=std::min({l[0],l[1],l[2]});
        result.first*=std::exp(.50*(.42+lowest-l[0]));
        result.second*=std::exp(.50*(.42+lowest-l[1]));
        result.additive*=std::exp(.50*(.42+lowest-l[2]));
        if(lowest>1.50)result.unknown*=1.5;
        ++used;
    }
    return result;
}

int blocker_cap(int sq,int n,int rival) {
    const auto& d=board[sq];int g=d.group;
    if(g==Rail) return 210+n*105+(rival>=2?110:0);
    if(g==Utility) return n?200:120;
    bool set=n+1==group_size[g],denies=rival+1==group_size[g];
    if(set || denies) {
        int sum=0;for(int i=0;i<group_size[g];++i) sum+=board[group_squares[g][i]].rents[3];
        int excess=std::max(0,sum-group_size[g]*d.house);
        return d.price+(set?excess*2/3:excess/2);
    }
    return d.price+(!rival?(n?d.price*2/3:90):!n?65:-d.price/4);
}
// Evidence that the price model for the known blocker can no longer explain the public history.
bool off_blocker(const State& s,int who) {
    for(int p=0;p<2;++p) {
        const auto& history=s.behavior[p];
        for(int i=history.size-1;i>=0;--i) {
            const auto& e=history.events[i];
            if(p!=who && (e.kind==ProposedTrade || e.kind==AcceptedTrade ||
                          e.kind==ProposedBundle || e.kind==AcceptedBundle)) return true;
            if(e.kind!=WonAuction && !(p!=who && e.kind==BoughtAtFace)) continue;
            if(e.square<0 || e.square>=Squares || board[e.square].group<0) continue;
            int g=board[e.square].group, high=0;
            for(int a=0;a<=s.count[1-who][g]-(p!=who);++a)
                for(int b=0;b<=s.count[who][g]-(p==who) && a+b<group_size[g];++b)
                    high=std::max(high,blocker_cap(e.square,a,b));
            if(e.amount>high+int(p==who)) return true;
        }
    }
    return false;
}
double utility(int cash,int cost,double stress) {
    int low=std::max(0,400-cash+cost)-std::max(0,400-cash);
    return cost+stress*low;
}
double deed(const State& s,int who,int sq,const Genes& w) {
    const auto& d=board[sq];int g=d.group,n=s.count[who][g],other=s.count[1-who][g];
    if(g==Rail) return 190.+(35*(1<<n))*(.9+2.4*w[DeedIncome]);
    if(g==Utility) return d.mortgage+(n?105.:45.)*(.75+w[DeedIncome]);
    double v=d.mortgage+d.rents[0]*(2.+3*w[DeedIncome]);
    if(n+1==group_size[g]) {
        int sum=0;for(int i=0;i<group_size[g];++i) sum+=board[group_squares[g][i]].rents[3];
        double liquidity=.55+.45*std::min(1.,(s.p[who].cash+d.mortgage)/(5.*d.house));
        return v+(.45+1.30*w[DeedIncome])*liquidity*(sum-1.25*d.house*group_size[g]);
    }
    if(n && other) return v*.77;
    return v+(n?250.:115.)*(.55+1.45*w[DeedIncome]);
}
void acquire(State& s,int who,int sq,int cost) {
    s.owner[sq]=who;++s.count[who][board[sq].group];s.p[who].cash-=cost;
}
struct Prices {
    std::array<Forecast,15> options{};
    int count=0;
    void add(int p,double weight) {
        if(weight<=0) return;
        for(int i=0;i<count;++i) if(options[i].price==p) {options[i].mass+=weight;return;}
        options[count++]={p,weight};
    }
};
bool passed_at_face(const State& s,int rival,const Query& q) {
    if(q.kind!=Kind::Bid || q.amount<0 || q.square<0 || q.square>=Squares)return false;
    const auto& h=s.behavior[rival];
    if(!h.size)return false;
    const auto& e=h.events[h.size-1];
    return e.kind==DeclinedFace && e.turn==s.turn && e.square==q.square;
}
Prices market(const State& s,int p,int sq,int block,int neural,int historical,const Genes& w,const Likelihood& posterior,
              bool direct_pass,int mode) {
    Prices m;
    int g=board[sq].group,cash=s.p[p].cash; const auto& d=board[sq];
    double a=.05+2.8*w[FirstRival], b=.05+2.8*w[SecondRival], c=.15+3*w[Additive],f=.14+1.8*w[OpenPopulation];
    a*=posterior.first;b*=posterior.second;c*=posterior.additive;f*=posterior.unknown;
    m.add(price(s,sq,p,block,neural,sparring[0]),a);
    m.add(price(s,sq,p,block,neural,sparring[1]),b);
    // The additive family prices ownership tiers and keeps a variable cash reserve.
    // Integrate several reserves and completion premiums; game-specific draws are unknown.
    for(double t:{.15,.50,.85}) {
        double quote=.60*neural+.40*d.price+35+42*s.count[p][g];
        if(g==Rail)quote+=100;
        if(g<Rail && s.count[1-p][g]+1==group_size[g]) quote+=220+220*t;
        if(g<Rail && s.count[p][g]+1==group_size[g]) quote+=370+180*(.50+.50*t);
        m.add(std::clamp(int(quote),0,std::max(0,cash-35-int(.70*t*cash*.22))),c/3);
    }
    m.add(block,.40);m.add(historical,.42);
    m.add(price(s,sq,p,block,neural,{.90,1.42,1.44,36}),f*.24);
    m.add(price(s,sq,p,block,neural,{.35,1.12,.86,105}),f*.25);
    m.add(price(s,sq,p,block,neural,{.60,1.0,1.1,60}),f*.26);
    m.add(price(s,sq,p,block,neural,{1.,.92,1.03,28}),f*.25);
    if(direct_pass && s.p[1-p].cash+1>=d.price) {
        // Both named hypotheses buy whenever their same-state bid reaches face
        // and the other seat could contest. An observed refusal then shifts mass
        // toward affordable bids below face, allowing for other rival strategies.
        for(int i=0;i<m.count;++i)if(m.options[i].price>=d.price)m.options[i].mass*=mode==5?.035:.25;
        m.add(std::min(cash,d.price-1),.42);
        m.add(std::min(cash,int(.72*d.price)),.21);
    }
    double total=0;
    for(int i=0;i<m.count;++i)total+=m.options[i].mass;
    for(int i=0;i<m.count;++i)m.options[i].mass/=total;
    return m;
}
Action purchase(const Policy& p,const State& s,int who,const Query& q,const Genes& w,int mode) {
    int sq=q.square,enemy=1-who;
    if(sq<0 || sq>=Squares || board[sq].group<0 || s.owner[sq]>=0) return {};
    int face=board[sq].price,cash=s.p[who].cash,enemy_cash=s.p[enemy].cash;
    Policy base=p;base.style=0;base.external=nullptr;
    Policy neural=base;neural.style=6;
    int hist=decide(base,s,enemy,{Kind::Bid,sq,10}).value;
    int block=holdout(s,enemy,{Kind::Bid,sq,10}).value;
    int rival=p.net?neural_decide(neural,s,enemy,{Kind::Bid,sq,10}).value:hist;
    Likelihood posterior=price_evidence(p,s,who,mode);
    if(mode>=4 && posterior.legacy_fit)return resilient_decide(p,s,who,q);
    Prices prices=market(s,enemy,sq,block,rival,hist,w,posterior,mode>=5 && passed_at_face(s,enemy,q),mode);
    double own=deed(s,who,sq,w), other=deed(s,enemy,sq,w);
    if(p.net) {
        const float start=neural_value(*p.net,s,who);
        State win=s,loss=s;int ours=std::min(face,cash),theirs=std::min(face,enemy_cash);
        acquire(win,who,sq,ours);acquire(loss,enemy,sq,theirs);
        double mix=.08+.65*w[NetworkMix],scale=1600.;
        own=(1-mix)*own+mix*(ours+scale*(neural_value(*p.net,win,who)-start));
        other=(1-mix)*other+mix*(theirs+scale*(start-neural_value(*p.net,loss,who)));
    }
    auto win=[&](int cost){return own-utility(cash,cost,2.*w[CashStress]);};
    auto lose=[&](int cost){return (.40+1.5*w[RivalSpend])*utility(enemy_cash,cost,w[CashStress])-other;};
    auto score=[&](int bid){
        double average=0,worst=1e12;
        for(int i=0;i<prices.count;++i) {
            auto o=prices.options[i];
            double reward=bid<10 && o.price<10?0.:bid==o.price?(win(bid)+lose(bid))*.5:
                bid>o.price?win(std::max(10,o.price+1)):lose(std::max(10,bid+1));
            average+=o.mass*reward;worst=std::min(worst,reward);
        }
        double downside=.35*w[Worst];
        return (1-downside)*average+downside*worst;
    };
    int best=0;double value=score(0);
    auto test=[&](int bid){bid=std::clamp(bid,0,cash);double v=score(bid);
        if(v>value+1e-7 || (std::abs(v-value)<1e-7 && bid<best)){best=bid;value=v;}};
    test(10);test(cash);
    for(int i=0;i<prices.count;++i){int z=prices.options[i].price;test(z-1);test(z);test(z+1);}
    if(q.kind==Kind::Bid) {
        if(current_bid_trace) explain_bid("Maximized a blend of expected and worst-case payoff across forecast rival ceilings; ties favor the lower bid.",
            {{"chosen_score",value},{"pass_score",score(0)},{"minimum_bid_score",score(10)},
             {"face_price_score",score(std::min(face,cash))},{"own_deed_value",own},{"opponent_deed_value",other}});
        return {best};
    }
    if(cash<face || enemy_cash<face-1)return {};
    double exposure=0;for(int i=0;i<prices.count;++i) if(prices.options[i].price>=face)exposure+=prices.options[i].mass;
    return {win(face)+(-80.+240*w[PurchaseRight])*exposure>=value};
}
Action financing(const Policy& p,const State& s,int who,const Genes& w,int mode) {
    int remaining=28;
    bool set=false,critical=false;
    for(int g=0;g<Groups;++g){
        int open=group_size[g]-s.count[0][g]-s.count[1][g];
        remaining-=group_size[g]-open;
        if(g<Rail) {
            set|=s.count[who][g]==group_size[g];
            critical|=open==1 && (s.count[who][g]+1==group_size[g] || s.count[1-who][g]+1==group_size[g]);
        }
    }
    auto incumbent=resilient_decide(p,s,who,{Kind::Manage});
    if(!remaining)return incumbent;
    int buffer=int((100.+900*w[WorkingCash])*(.30+.70*std::min(remaining,12)/12.));
    if(critical)buffer+=int(125*w[WorkingCash]);
    if(set)buffer=int(buffer*(.14+.60*w[HousingPriority]));
    if(incumbent.value==Build && (s.level[incumbent.square]<2 || s.p[who].cash-board[incumbent.square].house>=buffer))
        return incumbent;
    if(s.p[who].cash<buffer) {
        double cheapest=1e12;Action loan;
        for(int sq=0;sq<Squares;++sq) {
            if(s.owner[sq]!=who || !can_mortgage(s,who,sq))continue;
            int g=board[sq].group;
            if(g<Rail && s.count[who][g]==group_size[g])continue;
            double cost=(2.+rent(s,sq,7))/board[sq].mortgage;
            if(g==Rail && s.count[who][g]>=2)cost+=.70;
            if(cost<cheapest){cheapest=cost;loan={Mortgage,sq};}
        }
        if(loan.value==Mortgage)return loan;
    }
    if(incumbent.value==Redeem) {
        const auto& d=board[incumbent.square];
        const bool complete=s.count[who][d.group]==group_size[d.group];
        if(!complete && s.p[who].cash-d.mortgage*11/10<buffer+75)return {};
        // A completed utility or railroad group can supply working cash. Leave
        // enough cash after redemption to avoid immediately borrowing it again.
        if(mode>=4 && d.group>=Rail && s.p[who].cash-d.mortgage-(d.mortgage+9)/10<buffer)return {};
    }
    return incumbent.value==Build?Action{}:incumbent;
}
}
Action robust_trial(const Policy& p,const State& s,int who,const Query& q,const RobustWeights& model,int mode) {
    if(who<0 || who>1)return {};
    bool property=q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0);
    bool manage=q.kind==Kind::Manage && q.square<0;
    if(!property && !manage)return resilient_decide(p,s,who,q);
    const bool live_contradiction=mode>=5 && passed_at_face(s,1-who,q) && board[q.square].group>=0 &&
                                  holdout(s,1-who,{Kind::Buy,q.square,board[q.square].price}).value==1;
    if(!live_contradiction && !off_blocker(s,who))return resilient_decide(p,s,who,q);
    if(property)return purchase(p,s,who,q,model.w,mode);
    return financing(p,s,who,model.w,mode);
}
Action robust_decide(const Policy& p,const State& s,int who,const Query& q,const RobustWeights& model) {
    return robust_trial(p,s,who,q,model,RobustEvidenceMode);
}
} // namespace mono
