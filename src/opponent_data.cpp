// Train an observer from the public event stream of a legal game. The switch schedules
// label training and evaluation only; neither the engine's history nor the predictor sees them.
#include "opponent.hpp"
#include <fstream>
#include <iostream>
using namespace mono;
namespace {
struct Profile {
    Policy base;
    Rng rng;
    int change_turn;
    bool refuses, aggressive, change_trade, change_buy, probing;
    int reservation;
};
bool refusal(const Profile& p,int turn) { return p.refuses ^ (p.change_trade && turn>=p.change_turn); }
bool buying(const Profile& p,int turn) { return p.aggressive ^ (p.change_buy && turn>=p.change_turn); }

Action sample_offer(const State& s,int who,Profile& p) {
    int deeds[2][28], count[2]{};
    for (int q=0;q<Squares;++q) {
        int owner=s.owner[q];
        if (owner>=0 && !s.level[q]) deeds[owner][count[owner]++]=q;
    }
    for (int attempt=0;attempt<6;++attempt) {
        int give=count[who]?deeds[who][p.rng.below(count[who])]:-1;
        int take=count[1-who]?deeds[1-who][p.rng.below(count[1-who])]:-1;
        if (give<0 && take<0) return {};
        if (give>=0 && take>=0) {
            const int form=p.rng.below(4);
            if (form==0) give=-1;
            if (form==1) take=-1;
        }
        Action a{1,-1,give,take,0};
        const int face=(take<0?0:board[take].price)-(give<0?0:board[give].price);
        // Some proposers make one-sided demands repeatedly; others split surplus or pay a premium.
        int dollars=face + p.reservation + p.rng.below(401)-200;
        if (p.rng.below(3)==0) dollars-=offer_generosity(s,who,a)/2;
        if (p.rng.below(8)==0) dollars=0;
        a.cash=std::clamp(5*(dollars/5),-s.p[1-who].cash,s.p[who].cash);
        if (legal_trade(s,who,a)) return a;
    }
    return {};
}

Action behave(void* ctx,const State& s,int who,const Query& q) {
    auto& p=*static_cast<Profile*>(ctx);
    const bool shut=refusal(p,s.turn), high=buying(p,s.turn);
    if (q.kind==Kind::Offer) {
        if(shut) return {};
        if(p.rng.below(100)<(p.probing?47:18)) return sample_offer(s,who,p);
        return decide(p.base,s,who,q);
    }
    if (q.kind==Kind::Accept) {
        if(shut) return {};
        // Mix a strategic recipient with a recipient using an appraisal and reservation margin.
        if (p.reservation>=0 || p.rng.below(3)==0) return decide(p.base,s,who,q);
        const Action offer{1,-1,q.give,q.take,q.cash};
        return {offer_generosity(s,1-who,offer)>p.reservation/5+30};
    }
    if (q.kind==Kind::Buy || (q.kind==Kind::Bid && q.amount>=0)) {
        int base=decide(p.base,s,who,{Kind::Bid,q.square,10}).value;
        const bool cash_rich=s.p[who].cash>700;
        int limit=high ? int(base*1.35)+45 : int(base*.61)+(cash_rich?25:0);
        limit=std::clamp(limit,0,std::max(0,s.p[who].cash-(high?15:130)));
        return {q.kind==Kind::Buy ? int(limit>=board[q.square].price) : limit};
    }
    return decide(p.base,s,who,q);
}
}
int main(int argc,char** argv) {
    if(argc!=4) return 2;
    const int games=std::stoi(argv[1]);
    const uint64_t seed=std::stoull(argv[2]);
    std::ofstream dst(argv[3],std::ios::binary);
    if (!dst || games<1) return 2;
    int64_t observations=0,capped=0,switches=0;
    for(int i=0;i<games;++i) {
        Rng root{seed+uint64_t(i)};
        Rng sample{root.next()};
        auto profile=[&](bool challenger) {
            Policy base=preset(sample.below(4));
            if(base.style==0) for(auto& w:base.w) w=sample.below(1001)/1000.;
            return Profile{base,Rng{sample.next()},8+sample.below(80),
                !challenger && sample.below(2)==0,sample.below(2)==0,
                !challenger && sample.below(4)!=0,!challenger && sample.below(3)!=0,
                challenger,sample.below(701)-350};
        };
        const int p=sample.below(2);
        Profile rival=profile(false), challenger=profile(true);
        Policy a=rival.base,b=challenger.base;
        a.external=b.external=behave;
        a.context=&rival; b.context=&challenger;
        Game game(root.next(),p==0?a:b,p==0?b:a);
        int after=0, informative_after=0, previous=-1;
        while(game.s.turn<300 && game.s.winner<0) {
            game.turn();
            const auto& h=game.s.behavior[p];
            for(int j=0;j<h.size;++j) {
                const auto& e=h.events[j];
                if(e.turn!=game.s.turn) continue;
                auto x=opponent_features(e,previous);
                previous=e.turn;
                if(e.turn>=rival.change_turn) {
                    ++after;
                    informative_after+=(e.kind==1 || e.kind==2 || (e.kind==0 && e.generosity>50));
                }
                // All model features are public and precede both hidden-regime labels.
                dst.write(reinterpret_cast<char*>(x.data()),sizeof(x));
                float meta[]={float(i),float(refusal(rival,e.turn)),float(buying(rival,e.turn)),
                    float(rival.change_trade && after ? after : 0),
                    float(rival.change_buy && after ? after : 0),
                    float(rival.change_trade && after ? informative_after : 0)};
                dst.write(reinterpret_cast<char*>(meta),sizeof(meta));
                ++observations;
            }
        }
        switches+=rival.change_trade && after>0;
        capped+=game.s.winner<0;
    }
    dst.close();
    if(!dst) return 3;
    std::cout<<games<<" "<<observations<<" "<<switches<<" "<<capped<<"\n";
}
