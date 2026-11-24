#include "bid_trace.hpp"
#include "game.hpp"
#include "neural_value.hpp"
#include "neural_trade.hpp"
#include "response.hpp"
#include "opponent.hpp"
#include "adaptive_policy.hpp"
#include "robust_policy.hpp"
#include "bundle_trade.hpp"
#include "bundles/verified.hpp"
#include "bundle_match.hpp"
#include "deal_value.hpp"
#include <memory>
#include <algorithm>
#include <cstdint>

using namespace mono;

// All fields shared with ctypes use C scalar types. The deck order and random
// generator deliberately live outside the public snapshot.
extern "C" {
struct Snapshot {
    int cash[2], position[2], jail[2], cards[2];
    int owner[40], level[40], mortgaged[40];
    int houses, hotels, turn, active;
    int rejected[2][4];
    int history_size[2], history[2][16][7];
};
struct Request { int kind, square, amount, give, take, cash; };
struct Reply { int value, square, give, take, cash; };
struct Config { int style; const double* weights; const NeuralNet* net; const ResponseNet* response; const OpponentNet* opponent; const Specialist* specialist; const ResilienceWeights* resilience; const RobustWeights* robust; const DealNet* deal_net; const PressureWeights* pressure; };
struct Summary {
    int64_t wins, losses, caps, turns, first_wins, first_games;
    int64_t offers_sent, outgoing_accepted, offers_received, incoming_accepted;
};
typedef int (*DecisionFn)(const Snapshot*, int, const Request*, Reply*);
typedef int (*TableEventFn)(const Snapshot*, int, int, int, int);
}
namespace {
Policy policy_for(Config c) {
    Policy p = preset(c.style);
    if (c.weights && (c.style == 0 || c.style >= 5))
        std::copy_n(c.weights,Params,p.w.begin());
    p.net = c.net;
    p.response = c.response;
    p.opponent = c.opponent;
    p.specialist = c.specialist;
    p.resilience = c.resilience;
    p.robust = c.robust;
    p.deal_net = c.deal_net;
    p.pressure = c.pressure;
    return p;
}
Snapshot publish(const State& s) {
    Snapshot v{};
    for (int i=0;i<2;++i) {
        v.cash[i]=s.p[i].cash;
        v.position[i]=s.p[i].pos;
        v.jail[i]=s.p[i].jail;
        v.cards[i]=s.p[i].cards;
        v.history_size[i]=s.behavior[i].size;
        for (int j=0;j<v.history_size[i];++j) {
            const auto& e=s.behavior[i].events[j];
            const int values[]{e.kind,e.square,e.amount,e.turn,e.give,e.take,e.generosity};
            std::copy_n(values,7,v.history[i][j]);
        }
        const auto& offer=s.rejected[i];
        v.rejected[i][0]=offer.give; v.rejected[i][1]=offer.take;
        v.rejected[i][2]=offer.cash; v.rejected[i][3]=offer.turn;
    }
    std::copy(s.owner.begin(),s.owner.end(),v.owner);
    std::copy(s.level.begin(),s.level.end(),v.level);
    std::copy(s.mort.begin(),s.mort.end(),v.mortgaged);
    v.houses=s.houses; v.hotels=s.hotels; v.turn=s.turn; v.active=s.active;
    return v;
}
struct Disconnected {};
void table_event(void* context,const State& s,TableEvent event,int who,int a,int b) {
    const auto snapshot=publish(s);
    if (!(*static_cast<TableEventFn*>(context))(&snapshot,int(event),who,a,b)) throw Disconnected{};
}
Action external(void* context,const State& s,int who,const Query& q) {
    Snapshot v=publish(s);
    Request req{int(q.kind),q.square,q.amount,q.give,q.take,q.cash};
    Reply r{0,-1,-1,-1,0};
    if (!(*static_cast<DecisionFn*>(context))(&v,who,&req,&r)) throw Disconnected{};
    return {r.value,r.square,r.give,r.take,r.cash};
}
}
extern "C" {
const char* mono_param(int i) { return param_names.at(i); }
void mono_default(int style,double* weights) {
    const Policy p=preset(style);
    std::copy(p.w.begin(),p.w.end(),weights);
}
const char* mono_space(int i,int* fields) {
    const Space& b=board.at(i);
    fields[0]=b.group; fields[1]=b.price; fields[2]=b.mortgage; fields[3]=b.house;
    std::copy(b.rents.begin(),b.rents.end(),fields+4);
    return b.name;
}
void mono_batch(Config a,Config b,
                uint64_t seed,int games,int cap,int paired,Summary* out) {
    const auto pa=policy_for(a), pb=policy_for(b);
    Rng seeds{seed};
    *out={};
    uint64_t game_seed=0;
    for (int i=0;i<games;i++) {
        const int seat=i%2;
        if (!paired || !seat) game_seed=seeds.next();
        Game game(game_seed,seat ? pb : pa,seat ? pa : pb);
        const int winner=game.play(cap);
        if (!seat) ++out->first_games;
        if (winner == seat) { ++out->wins; if (!seat) ++out->first_wins; }
        else if (winner == -1) ++out->caps;
        else ++out->losses;
        out->turns+=game.s.turn;
        out->offers_sent+=game.offers_made[seat];
        out->outgoing_accepted+=game.offers_closed[seat];
        out->offers_received+=game.offers_made[1-seat];
        out->incoming_accepted+=game.offers_closed[1-seat];
    }
}
int mono_external(Config cfg,uint64_t seed,int cap,int rival_seat,
                  DecisionFn cb,int* turns) {
    const Policy ours=policy_for(cfg);
    Policy rival{};
    rival.external=external; rival.context=&cb;
    Game game(seed,rival_seat ? ours : rival,rival_seat ? rival : ours);
    try {
        int winner=game.play(cap);
        *turns=game.s.turn;
        return winner;
    } catch (const Disconnected&) { return -2; }
}
int mono_table(uint64_t seed,int cap,DecisionFn decide_fn,TableEventFn event_fn,Snapshot* final_state) {
    Policy player{};
    player.external=external;
    player.context=&decide_fn;
    Game game(seed,player,player);
    game.table_observer=table_event;
    game.table_context=&event_fn;
    try {
        int winner=game.play(cap);
        *final_state=publish(game.s);
        return winner;
    } catch (const Disconnected&) { return -2; }
}
static State read_state(const Snapshot* v) {
    // The Python protocol reader has checked all indices and owner values.
    State s{};
    for (int i=0;i<2;i++) s.p[i]={v->cash[i],v->position[i],v->jail[i],unsigned(v->cards[i])};
    for (int who=0;who<2;++who) {
        for (int j=0;j<std::clamp(v->history_size[who],0,16);++j) {
            const int* e=v->history[who][j];
            s.behavior[who].append({e[0],e[1],e[2],e[3],e[4],e[5],e[6]});
        }
        auto r=v->rejected[who];
        if (r[3]>0) s.rejected[who]={r[0],r[1],r[2],r[3]};
    }
    for (int i=0;i<Squares;i++) {
        s.owner[i]=v->owner[i]; s.level[i]=v->level[i]; s.mort[i]=v->mortgaged[i]!=0;
        const int owner=s.owner[i], group=board[i].group;
        if (owner>=0 && group>=0) {
            ++s.count[owner][group];
            s.mort_count[owner]+=s.mort[i];
        }
    }
    s.houses=v->houses; s.hotels=v->hotels; s.turn=v->turn; s.active=v->active;
    return s;
}
void mono_table_management(const Snapshot* v,int who,int* actions) {
    const auto s=read_state(v);
    for (int i=0;i<Squares;++i) {
        actions[i]=(can_build(s,who,i) ? (1<<Build) : 0)
                 | (can_sell(s,who,i) ? (1<<Sell) : 0)
                 | (can_mortgage(s,who,i) ? (1<<Mortgage) : 0)
                 | (can_redeem(s,who,i) ? (1<<Redeem) : 0);
    }
}
NeuralNet* mono_load_network(const char* path) {
    auto net=std::make_unique<NeuralNet>();
    return load_neural(path,*net) ? net.release() : nullptr;
}
ResponseNet* mono_load_response(const char* path) {
    auto net=std::make_unique<ResponseNet>();
    return load_response(path,*net) ? net.release() : nullptr;
}
OpponentNet* mono_load_opponent(const char* path) {
    auto net=std::make_unique<OpponentNet>();
    return load_opponent(path,*net) ? net.release() : nullptr;
}
void mono_free_opponent(OpponentNet* net) { delete net; }
void mono_scout(Config cfg,const Snapshot* v,int seat,float* result) {
    const auto policy=policy_for(cfg);
    const auto state=read_state(v);
    result[0]=refusal_probability(policy,state,seat);
    result[1]=opponent_aggressiveness(policy,state,seat);
}
int mono_generosity(const Snapshot* v,int proposer,int give,int take,int cash) {
    return offer_generosity(read_state(v),proposer,{1,-1,give,take,cash});
}
DealNet* mono_load_deal(const char* path) {
    auto net=std::make_unique<DealNet>();
    return load_deal_net(path,*net) ? net.release() : nullptr;
}
void mono_free_deal(DealNet* net) { delete net; }
void mono_free_response(ResponseNet* net) { delete net; }
void mono_free_network(NeuralNet* net) { delete net; }
int mono_bundle_matches(Config a,Config b,uint64_t seed,int games,int cap,BundleStats* totals) {
    if ((!a.net && !b.net) || games<=0 || cap<=0) return 0;
    *totals={};
    try { bundle_matches(policy_for(a),policy_for(b),seed,games,cap,*totals); }
    catch (const std::exception&) { return 0; }
    return 1;
}
int mono_bundle_assess(Config c,const Snapshot* v,int proposer,uint64_t give,uint64_t take,int cash,BundleResult* out) {
    const auto p=policy_for(c);
    return package_assessment(p,read_state(v),proposer,give,take,cash,*out);
}
int mono_bundle_offer(Config c,const Snapshot* v,int proposer,BundleResult* out) {
    return package_offer(policy_for(c),read_state(v),proposer,*out);
}
void mono_set_bid_trace(BidTrace* trace) { current_bid_trace=trace; }
void mono_decide(Config cfg,const Snapshot* v,int who,const Request* q,Reply* r) {
    auto a=decide(policy_for(cfg),read_state(v),who,
                  {Kind(q->kind),q->square,q->amount,q->give,q->take,q->cash});
    *r={a.value,a.square,a.give,a.take,a.cash};
}
// 'who' receives the offer. Monetary and deed fields keep their proposer perspective.
int mono_assess(Config cfg,const Snapshot* v,int who,const Request* q,float* scores,int* accept) {
    if (!cfg.net || Kind(q->kind)!=Kind::Accept) return 0;
    const Policy policy=policy_for(cfg);
    auto s=read_state(v);
    const Action offer{1,-1,q->give,q->take,q->cash};
    const auto a=assess_trade(policy,s,1-who,offer);
    if (!a.legal) return 0;
    scores[0]=a.proposer_before; scores[1]=a.proposer_after;
    scores[2]=a.recipient_before; scores[3]=a.recipient_after;
    scores[4]=a.acceptance_estimate; scores[5]=a.expected_gain;
    scores[6]=a.recipient_deal_gain; scores[7]=a.recipient_uses_deal_model;
    *accept=decide(policy,s,who,{Kind::Accept,-1,0,q->give,q->take,q->cash}).value;
    return 1;
}
}
