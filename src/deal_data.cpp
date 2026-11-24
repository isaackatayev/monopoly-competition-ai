#include "deal_value.hpp"
#include "neural_trade.hpp"
#include <fstream>
#include <iostream>
#include <string>
using namespace mono;
struct Continuation { Policy agent; };
Action without_deals(void* raw,const State& s,int who,const Query& q) {
    if(q.kind==Kind::Offer) return {};
    return decide(static_cast<Continuation*>(raw)->agent,s,who,q);
}
Action sample(const Policy& prior,const State& s,int p,Rng& rng) {
    int owned[2][28],n[2]{};
    for(int i=0;i<Squares;++i) if(s.owner[i]>=0) owned[s.owner[i]][n[s.owner[i]]++]=i;
    for(int t=0;t<18;++t) {
        Action a=t==0 && rng.below(3) ? decide(prior,s,p,{Kind::Offer}):Action{};
        if(a.give<0 && a.take<0) {
            if(n[p] && rng.below(3)) a.give=owned[p][rng.below(n[p])];
            if(n[1-p] && (a.give<0 || rng.below(3))) a.take=owned[1-p][rng.below(n[1-p])];
            if(a.give<0 && a.take<0) continue;
            int face=(a.take<0?0:board[a.take].price)-(a.give<0?0:board[a.give].price);
            if(rng.below(3)==0) a.cash=face+rng.below(201)-100;
            else if(rng.below(3)) {
                double m=trade_deed_value(prior,s,p,a.give,false)+trade_deed_value(prior,s,p,a.take,true);
                double other=trade_deed_value(prior,s,1-p,a.give,true)+trade_deed_value(prior,s,1-p,a.take,false);
                a.cash=int(-other+(m+other)*rng.below(101)/100.)+rng.below(201)-100;
            }
        } else a.cash+=rng.below(201)-100;
        a.cash=std::clamp(a.cash,-s.p[1-p].cash,s.p[p].cash);
        if(legal_trade(s,p,a)) return a;
    }
    return {};
}
float paired_score(const NeuralNet& net,const State& s,int recipient,const Policy& a,const Policy& b,uint64_t seed) {
    Game g(seed,a,b);g.s=s;condition_decks_on_held_cards(g);
    // s is at the end of the last turn, immediately preceding this offer.
    g.play(s.turn+170);
    return neural_value(net,g.s,recipient);
}
int main(int argc,char** argv) {
    if(argc!=6)return 2;
    int games=std::stoi(argv[1]),trials=std::stoi(argv[3]);Rng seeds{std::stoull(argv[2])};
    NeuralNet net; if(!load_neural(argv[4],net)) return 3;
    std::ofstream out(argv[5],std::ios::binary);
    size_t rows=0;
    for(int i=0;i<games;++i){
        Rng rng{seeds.next()}; Policy prior=preset(rng.below(4));
        Policy a=preset(rng.below(5)),b=preset(rng.below(5));
        Policy nn=prior;nn.net=&net;
        Game g(rng.next(),a,b); int target=6+rng.below(75);
        g.play(target); if(g.s.winner>=0)continue;
        int p=g.s.active;auto deal=sample(prior,g.s,p,rng); if(!legal_trade(g.s,p,deal)) continue;
        const auto s=g.s; auto after=s;
        // Settlement elections must use the receiving policy in each seat.
        settle_trade(after,p,deal,p?b:a,p?a:b);
        auto x=deal_features(nn,s,after,1-p,deal);
        float gain=0;
        for(int t=0;t<trials;++t) {
            Continuation ca{preset(rng.below(5))},cb{preset(rng.below(5))};
            Policy pa=ca.agent,pb=cb.agent;pa.external=pb.external=without_deals;
            pa.context=&ca;pb.context=&cb;auto seed=rng.next();
            gain+=paired_score(net,after,1-p,pa,pb,seed)-paired_score(net,s,1-p,pa,pb,seed);
        }
        gain/=trials;out.write(reinterpret_cast<const char*>(x.data()),sizeof(x));
        out.write(reinterpret_cast<const char*>(&gain),sizeof(gain));++rows;
    }
    std::cout<<rows<<'\n'; return !out;
}
