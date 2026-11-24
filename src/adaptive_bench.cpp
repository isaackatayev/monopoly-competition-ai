// Standalone paired tournament driver. The fixture identities, transition
// thresholds and modes live here; none are passed to the competing policy.
#include "adaptive_policy.hpp"
#include "neural_value.hpp"
#include "opponent.hpp"
#include "response.hpp"
#include "groups.hpp"

#include <array>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace mono;
namespace {
enum Fixture { Collector, Builder, Cautious, Legacy, Blocker, Frozen, Incumbent, Close, Open, Reactive, Independent, Learned, NumFixtures };
const char* fixture_names[]={"collector","builder","cautious","legacy","blocker","frozen_neural","incumbent_v1",
                             "trading_then_refusal","refusal_then_trading","reactive","independent_bidder","learned_rival"};
using RivalWeights=std::array<double,6>;
struct Challenger {
    const Policy& neural;
    const RivalWeights& w;
};

Action learned_rival(void* raw,const State& s,int who,const Query& q) {
    const auto& m=*static_cast<Challenger*>(raw);
    const auto& w=m.w;
    if(q.kind==Kind::Offer && w[4]<.66) return {};
    if(q.kind==Kind::Accept && w[4]<.33) return {};
    if(q.kind==Kind::Offer || q.kind==Kind::Accept) return decide(m.neural,s,who,q);
    if(q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0)) {
        const int sq=q.square,g=board[sq].group;
        Query bid{Kind::Bid,sq,10};
        double block=holdout(s,who,bid).value;
        double neural=w[0]>0 ? decide(m.neural,s,who,bid).value : block;
        double premium=.5+w[1];
        if(s.count[1-who][g]+1==group_size[g]) premium*=.5+w[2];
        int cap=std::max(0,s.p[who].cash-int(240*w[3]));
        int max_bid=std::clamp(int(((1.-w[0])*block+w[0]*neural)*premium),0,cap);
        if(q.kind==Kind::Bid) return {max_bid};
        if(s.p[1-who].cash+1<board[sq].price) return {};
        return {max_bid>=board[sq].price};
    }
    return w[5]>=.5 ? decide(m.neural,s,who,q) : holdout(s,who,q);
}

struct Market {
    Fixture fixture;
    Policy ordinary;
    int switch_turn;
    int cooldown;
    int bad_offers=0, reopen_at=0, previous=-1, changes=0, decisions[2]{};
    int open_trade_actions=0,suppressed_trade_actions=0;
};

// This untuned, trade-free fixture prices the effect of ownership on expected
// rent and holds a reserve proportional to the largest collectible enemy rent.
int independent_ceiling(const State& s,int who,int sq) {
    int cash=s.p[who].cash;
    if (sq<0 || sq>=Squares || board[sq].group<0 || !cash) return 0;
    int danger=0;
    for (int q=0;q<Squares;++q)
        if (s.owner[q]==1-who) danger=std::max(danger,rent(s,q,7));
    const auto& d=board[sq];
    int g=d.group, n=s.count[who][g], enemy=s.count[1-who][g];
    double value=d.mortgage+14*d.rents[0];
    if (g==Rail) value=155+80*n+45*enemy;
    else if (g==Utility) value=n?245:120;
    else {
        if (n+1==group_size[g]) value+=std::max(100,d.rents[3]-d.house)+.15*s.p[who].cash;
        else if (n && !enemy) value+=d.price*.60;
        else if (!enemy) value+=d.price*.22;
        if (enemy+1==group_size[g]) value+=d.rents[3]*.63;
    }
    return std::clamp(int(value),0,std::max(0,cash-40-danger/6));
}

bool closed_mode(Market& m,const State& s) {
    if (m.fixture==Close) return s.turn>=m.switch_turn;
    if (m.fixture==Open) return s.turn<m.switch_turn;
    return s.turn<m.reopen_at;
}

Action fixture_decision(void* raw,const State& s,int who,const Query& q) {
    auto& m=*static_cast<Market*>(raw);
    if (m.fixture==Independent) {
        if (q.kind==Kind::Offer || q.kind==Kind::Accept) return {};
        if (q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0)) {
            int price=independent_ceiling(s,who,q.square);
            return {q.kind==Kind::Buy ? int(price>=board[q.square].price) : price};
        }
        return decide(m.ordinary,s,who,q);
    }
    bool closed=closed_mode(m,s);
    if (m.previous!=-1 && closed!=bool(m.previous)) ++m.changes;
    m.previous=closed;
    ++m.decisions[closed?1:0];
    if (q.kind==Kind::Accept) {
        auto answer=decide(m.ordinary,s,who,q);
        if (closed) {m.suppressed_trade_actions+=answer.value==1;return {};}
        m.open_trade_actions+=answer.value==1;
        if (m.fixture==Reactive) {
            // A successful agreement restores trust. Two personally unacceptable
            // offers trigger a cooling-off interval. The fixture then opens again.
            m.bad_offers=answer.value ? 0 : m.bad_offers+1;
            if (m.bad_offers>=2) {
                m.reopen_at=s.turn+m.cooldown;
                m.bad_offers=0;
            }
        }
        return answer;
    }
    if (q.kind==Kind::Offer) {
        auto action=decide(m.ordinary,s,who,q);
        const bool offered=legal_trade(s,who,action);
        if (closed) {m.suppressed_trade_actions+=offered;return {};}
        m.open_trade_actions+=offered;
        return action;
    }
    if ((q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0)) && closed) {
        // In the closed regime, independently finance deeds and defend more aggressively.
        int cap=independent_ceiling(s,who,q.square);
        return {q.kind==Kind::Buy ? int(cap>=board[q.square].price) : cap};
    }
    return decide(m.ordinary,s,who,q);
}

bool same(const Action& a,const Action& b) {
    return a.value==b.value && a.square==b.square && a.cash==b.cash && a.give==b.give && a.take==b.take;
}
struct Audit {
    const Policy* entry;
    bool on;
    int seen[7]{}, different[7]{};
};
Action watch(void* raw,const State& s,int who,const Query& q) {
    auto& a=*static_cast<Audit*>(raw);
    Action actual=decide(*a.entry,s,who,q);
    if (a.on && s.behavior[1-who].size>0) {
        // Counterfactual at an actual decision: preserve the exact position,
        // own history and last rejected terms. Remove only rival event history.
        State empty=s;
        empty.behavior[1-who]={};
        int k=int(q.kind);
        ++a.seen[k];
        a.different[k]+=!same(actual,decide(*a.entry,empty,who,q));
    }
    return actual;
}

struct GameResult {
    int winner,turns,offered,closed,received,accepted;
    int transitions,open_decisions,closed_decisions,open_trade_actions,suppressed,seen[7],different[7];
};
GameResult play(const Policy& entrant,const Policy& prior,Fixture f,uint64_t seed,
                int seat,int threshold,int cooldown,int cap,bool audit,const RivalWeights& rival,const Policy& incumbent) {
    int style=(f==Collector ? 1 : f==Builder ? 2 : f==Cautious ? 3 : f==Blocker ? 4 : 0);
    Policy opp=preset(style);
    if (f==Legacy) {
        opp=prior;
        opp.style=5;
    }
    if(f==Frozen) opp=prior;
    if(f==Incumbent) opp=incumbent;
    Market m{f,preset(f==Independent ? 2 : 3),threshold,cooldown};
    Challenger learned{prior,rival};
    if(f==Learned) {
        opp.external=learned_rival;opp.context=&learned;
    } else if (f>=Close) {
        opp.external=fixture_decision;
        opp.context=&m;
    }
    Audit tracked{&entrant,audit};
    Policy actor=entrant;
    if (audit) {
        actor.external=watch;
        actor.context=&tracked;
    }
    Game g(seed,seat?opp:actor,seat?actor:opp);
    int w=g.play(cap);
    GameResult out{w<0?2:int(w!=seat),g.s.turn,g.offers_made[seat],
                   g.offers_closed[seat],g.offers_made[1-seat],g.offers_closed[1-seat],
                   m.changes,m.decisions[0],m.decisions[1],m.open_trade_actions,m.suppressed_trade_actions,{},{}};
    std::copy_n(tracked.seen,7,out.seen);
    std::copy_n(tracked.different,7,out.different);
    return out;
}

void read_resilience(Policy& adaptive,ResilienceWeights& weights) {
    // The optional marker keeps old v1 weight streams unchanged, including
    // their six trailing challenger weights. Training uses this same path.
    std::cin>>std::ws;
    if(std::cin.peek()!='v') return;
    std::string marker;
    if(!(std::cin>>marker) || marker!="v2") throw std::runtime_error("Unknown model extension");
    constexpr double lower[]{0,0,0,.5,.5,-150,0,0}, upper[]{1,1,1,2,2,150,1,1};
    for(int i=0;i<ResilienceParams;++i) {
        double& w=weights.w[i];
        if(!(std::cin>>w) || !(w>=lower[i] && w<=upper[i])) throw std::runtime_error("Invalid resilience weights");
    }
    adaptive.style=8;
    adaptive.resilience=&weights;
}

void print(const GameResult& r) {
    std::cout<<' '<<r.winner<<' '<<r.turns<<' '<<r.offered<<' '<<r.closed<<' '<<r.received
             <<' '<<r.accepted<<' '<<r.transitions<<' '<<r.open_decisions<<' '<<r.closed_decisions
             <<' '<<r.open_trade_actions<<' '<<r.suppressed;
    for(int i:r.seen) std::cout<<' '<<i;
    for(int i:r.different) std::cout<<' '<<i;
}

void print_witness(const Policy& adaptive) {
    // Controlled histories, all evaluated on the same public position. A second
    // test during actual matches clears the observed rival history in place.
    State base;
    base.owner.fill(-1);
    base.turn=52;base.active=0;base.p[0].cash=940;base.p[1].cash=760;
    base.p[0].pos=18;base.p[1].pos=33;
    for (int p=0;p<2;++p) {
        const int own[2][5]={{6,8,16,26,35},{9,18,19,29,5}};
        for(int sq:own[p]) {base.owner[sq]=p;++base.count[p][board[sq].group];}
    }
    std::array<State,4> cases{base,base,base,base};
    const int trade_deeds[]={1,3,11,13,14,23,24,31};
    for(int k=0;k<8;++k) {
        BehaviorEvent e;
        e.kind=AcceptedTrade;e.give=trade_deeds[k];e.amount=-60;e.turn=12+2*k;e.generosity=160;
        cases[1].behavior[1].append(e);
        e.kind=DeclinedTrade;
        cases[2].behavior[1].append(e);
        cases[3].behavior[1].append(e);
    }
    for(int k=0;k<8;++k) {
        BehaviorEvent e;
        e.kind=AcceptedTrade;e.give=trade_deeds[k];e.amount=-60;e.turn=30+2*k;e.generosity=160;
        cases[3].behavior[1].append(e);
    }
    int best=-1, best_sq=-1;
    std::array<int,4> chosen{};
    for(int sq=0;sq<Squares;++sq) {
        if(board[sq].group<0 || base.owner[sq]>=0) continue;
        std::array<int,4> bids{};
        for(int i=0;i<4;++i) bids[i]=decide(adaptive,cases[i],0,{Kind::Bid,sq,10}).value;
        int spread=std::abs(bids[1]-bids[2]), distance=std::abs(bids[1]-bids[3]);
        // Select the most visible reversal; report raw bids, even when the
        // initial market estimates happen to imply the same optimal ceiling.
        int score=spread + ((spread>distance)?5000:0);
        if(score>best) {best=score;best_sq=sq;chosen=bids;}
    }
    std::cout<<best_sq;
    for(int i=0;i<4;++i) std::cout<<' '<<chosen[i]<<' '<<refusal_probability(adaptive,cases[i],1);
    std::cout<<'\n';
}
}

int main(int argc,char** argv) {
    try {
        if (argc!=10) throw std::runtime_error("Supply fixture, seed, first pair, pairs, cap, value, response, opponent and condition seed");
        Fixture f=NumFixtures;
        for(int i=0;i<NumFixtures;++i) if(argv[1]==std::string(fixture_names[i])) f=Fixture(i);
        const bool witness=std::string(argv[1])=="witness",train=std::string(argv[1])=="rival_train";
        if(train) f=Learned;
        if(f==NumFixtures && !witness) throw std::runtime_error("Unknown fixture");
        uint64_t seed=std::stoull(argv[2]), fixture_seed=std::stoull(argv[9]);
        int first=std::stoi(argv[3]), pairs=std::stoi(argv[4]),cap=std::stoi(argv[5]);
        if(first<0 || pairs<=0 || cap<=0) throw std::runtime_error("Invalid range");
        NeuralNet value; ResponseNet response; OpponentNet opponent; Specialist specialist;
        if(!load_neural(argv[6],value)||!load_response(argv[7],response)||!load_opponent(argv[8],opponent))
            throw std::runtime_error("Could not load model weights");
        Policy fixed=preset(0);
        for(auto& w:fixed.w) if(!(std::cin>>w)) throw std::runtime_error("Missing prior weights");
        for(auto& w:specialist.w) if(!(std::cin>>w)) throw std::runtime_error("Missing specialist weights");
        fixed.style=6; fixed.net=&value; fixed.response=&response;
        Policy adaptive=fixed;adaptive.style=7;adaptive.specialist=&specialist;adaptive.opponent=&opponent;
        const Policy incumbent=adaptive;
        ResilienceWeights resilience;
        read_resilience(adaptive,resilience);
        const Policy& baseline=adaptive.style==8 ? incumbent : fixed;
        if(witness) {print_witness(adaptive);return 0;}
        RivalWeights rival{};
        if(f==Learned)
            for(double& w:rival) if(!(std::cin>>w) || !(w>=0 && w<=1)) throw std::runtime_error("Invalid challenger weights");
        Rng seeds{seed+uint64_t(first)*0x9e3779b97f4a7c15ULL};
        Rng regimes{fixture_seed+uint64_t(first)*0x9e3779b97f4a7c15ULL};
        int train_counts[3]{};
        for(int i=0;i<pairs;++i) {
            uint64_t game_seed=seeds.next(), transition=regimes.next();
            int threshold=18+int(transition%36), cooldown=8+int((transition>>32)%15);
            for(int seat=0;seat<2;++seat) {
                if(train) {
                    const auto r=play(adaptive,fixed,f,game_seed,seat,threshold,cooldown,cap,false,rival,incumbent);
                    ++train_counts[r.winner];
                    continue;
                }
                std::cout<<first+i<<' '<<seat;
                print(play(baseline,fixed,f,game_seed,seat,threshold,cooldown,cap,false,rival,incumbent));
                print(play(adaptive,fixed,f,game_seed,seat,threshold,cooldown,cap,(first+i)%50==0,rival,incumbent));
                std::cout<<'\n';
            }
        }
        if(train) std::cout<<train_counts[1]<<' '<<train_counts[0]<<' '<<train_counts[2]<<'\n';
        return 0;
    } catch(const std::exception& e) {
        std::cerr<<e.what()<<'\n';return 1;
    }
}
