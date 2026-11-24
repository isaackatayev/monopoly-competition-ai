#include "opponent.hpp"
#include <cmath>
#include <cstring>
#include <fstream>

namespace mono {
namespace {
float logistic(float x) { return 1.f / (1.f + std::exp(-std::clamp(x, -25.f, 25.f))); }

std::array<float,2> infer(const OpponentNet& net, const BehaviorHistory& events) {
    std::array<float,BehaviorHidden> h{};
    int previous = -1;
    for (int t = 0; t < events.size; ++t) {
        const auto& event = events.events[t];
        const auto features = opponent_features(event, previous);
        previous = event.turn;
        // Declining an obviously disadvantageous deal provides no evidence of a blanket veto.
        const float evidence = event.kind == DeclinedTrade || event.kind == DeclinedBundle ? features[7] : 1.f;
        auto pre = net.bias;
        std::array<float, BehaviorGates> from_hidden{};
        for (int i = 0; i < BehaviorInputs; ++i) {
            if (!features[i]) continue;
            for (int j = 0; j < BehaviorGates; ++j) pre[j] += features[i] * net.input[i*BehaviorGates+j];
        }
        for (int i = 0; i < BehaviorHidden; ++i) {
            for (int j = 0; j < BehaviorGates; ++j) from_hidden[j] += h[i] * net.recurrent[i*BehaviorGates+j];
        }
        for (int i = 0; i < BehaviorHidden; ++i) {
            const int r = i + BehaviorHidden, n = r + BehaviorHidden;
            const float update = logistic(pre[i]+from_hidden[i]);
            const float reset = logistic(pre[r]+from_hidden[r]);
            const float proposal = std::tanh(pre[n]+reset*from_hidden[n]);
            h[i] += evidence*(1.f-update)*(proposal-h[i]);
        }
    }
    auto logits = net.intercept;
    for (int i = 0; i < BehaviorHidden; ++i) {
        logits[0] += h[i]*net.output[i*2];
        logits[1] += h[i]*net.output[i*2+1];
    }
    return {logistic(logits[0]), logistic(logits[1])};
}
}

int offer_generosity(const State& s, int proposer, const Action& a) {
    if (proposer != 0 && proposer != 1) return 0;
    const int r = 1-proposer;
    const auto valid = [&](int q,int owner) {
        return q==-1 || (q>=0 && q<Squares && board[q].group>=0 && s.owner[q]==owner);
    };
    if (!valid(a.give,proposer) || !valid(a.take,r)) return 0;
    static const Policy reference = preset(0);
    double mine = a.cash;
    double theirs = -double(a.cash);
    if (a.give>=0 && a.take>=0 && board[a.give].group==board[a.take].group) {
        auto equity=[&](int q) {return (s.mort[q]?0:board[q].mortgage)+2.*board[q].rents[0];};
        const double diff=equity(a.give)-equity(a.take);
        mine+=diff; theirs-=diff;
    } else {
        mine += trade_deed_value(reference,s,r,a.give,true) + trade_deed_value(reference,s,r,a.take,false);
        theirs += trade_deed_value(reference,s,proposer,a.take,true) + trade_deed_value(reference,s,proposer,a.give,false);
    }
    return int(std::clamp(std::round(mine-.45*std::max(0.,theirs)), -2000., 2000.));
}

EventFeatures opponent_features(const BehaviorEvent& e, int previous_turn) {
    EventFeatures x{};
    if(e.kind<DeclinedTrade || e.kind>ProposedBundle) return x;
    // Bundle observations share the publicly observed response, cash and
    // generosity channels. No individual deed stands for the entire package.
    const bool bundle = e.kind >= DeclinedBundle;
    const int kind = bundle ? e.kind-DeclinedBundle : e.kind;
    x[kind]=1;
    const bool trade=kind<=ProposedTrade;
    if(trade) {
        x[6]=std::clamp(e.generosity/220.f,-3.f,3.f);
        x[7]=kind==DeclinedTrade && e.generosity > -180 ? logistic((e.generosity-20)/65.f) : 0.f;
        x[8]=std::clamp(e.amount/400.f,-3.f,3.f);
        x[14]=!bundle && e.give>=0;
        x[15]=!bundle && e.take>=0;
    }
    if(!bundle && e.square>=0 && e.square<Squares && board[e.square].group>=0) {
        const auto& deed=board[e.square];
        x[9]=deed.price/400.f;
        if(e.kind==5) x[10]=std::clamp(float(e.amount)/deed.price-1,-1.f,3.f);
        x[11]=deed.group==Rail;
        x[12]=deed.group==Utility;
    }
    x[13]=previous_turn<0 ? 0.f : std::clamp((e.turn-previous_turn)/16.f,0.f,3.f);
    return x;
}

bool load_opponent(const char* path, OpponentNet& destination) {
    std::ifstream in(path,std::ios::binary);
    char magic[8];
    if (!in.read(magic,8) || std::memcmp(magic,"MOPPGR01",8)) return false;
    OpponentNet net;
    auto read = [&](auto& weights) {
        for (float& v : weights) {
            unsigned char b[4];
            if (!in.read(reinterpret_cast<char*>(b),4)) return false;
            uint32_t u=uint32_t(b[0]) | uint32_t(b[1])<<8 | uint32_t(b[2])<<16 | uint32_t(b[3])<<24;
            std::memcpy(&v,&u,4);
            if (!std::isfinite(v) || std::abs(v)>100.f) return false;
        }
        return true;
    };
    if (!read(net.input) || !read(net.recurrent) || !read(net.bias) ||
        !read(net.output) || !read(net.intercept) || in.peek()!=std::ifstream::traits_type::eof()) return false;
    destination=net;
    return true;
}
std::array<float,2> opponent_beliefs(const Policy& p, const State& s, int seat) {
    if (!p.opponent || seat < 0 || seat > 1) return {.5f,.5f};
    const auto& history = s.behavior[seat];
    if (history.size < 1 || history.size > int(history.events.size())) return {.5f,.5f};
    // Both heads share a forward pass. Compare model contents as well as address, so
    // reloads, caller-owned model edits and recycled allocations cannot reuse a stale answer.
    struct Entry { BehaviorHistory history; std::array<float,2> prediction{}; };
    struct Cache { const OpponentNet* owner=nullptr; OpponentNet net; Entry seats[2]; };
    thread_local Cache cache;
    if (cache.owner != p.opponent || std::memcmp(&cache.net,p.opponent,sizeof(OpponentNet)) != 0) {
        cache.owner = p.opponent;
        cache.net = *p.opponent;
        for (auto& entry : cache.seats) entry.history.size = 0;
    }
    Entry& entry = cache.seats[seat];
    if (entry.history.size != history.size || std::memcmp(entry.history.events.data(),
            history.events.data(), size_t(history.size)*sizeof(BehaviorEvent)) != 0) {
        entry.history = history;
        entry.prediction = infer(cache.net,history);
    }
    return entry.prediction;
}

float refusal_probability(const Policy& p,const State& s,int seat) {return opponent_beliefs(p,s,seat)[0];}
float opponent_aggressiveness(const Policy& p,const State& s,int seat) {return opponent_beliefs(p,s,seat)[1];}
} // namespace mono
