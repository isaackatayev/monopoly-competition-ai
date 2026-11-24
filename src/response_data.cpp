// Generate recipient labels from legal offers actually delivered in simulated games.
#include "response.hpp"
#include <cstdlib>
#include <fstream>
#include <iostream>
using namespace mono;
namespace {
struct Recorder {
    Policy policy;
    Rng rng;
    std::ostream& file;
    bool refuses;
    int offered=0, accepted=0;
};
Action exploratory(const State& s,int p,Rng& r) {
    int deeds[2][28], n[2]{};
    for (int i=0;i<Squares;++i) {
        int owner=s.owner[i];
        if (owner>=0 && s.level[i]==0) deeds[owner][n[owner]++]=i;
    }
    for (int tries=0;tries<5;++tries) {
        int give=n[p] ? deeds[p][r.below(n[p])] : -1;
        int take=n[1-p] ? deeds[1-p][r.below(n[1-p])] : -1;
        if (give>=0 && take>=0) {
            int form=r.below(4);
            if (form==0) give=-1;
            if (form==1) take=-1;
        }
        if (give<0 && take<0) return {};
        int cash=(take>=0 ? board[take].price : 0)-(give>=0 ? board[give].price : 0);
        for (int i=0;i<2;++i) cash+=r.below(351)-175;
        if (r.below(7)==0) cash=0;
        cash=std::clamp(cash,-s.p[1-p].cash,s.p[p].cash);
        Action offer{1,-1,give,take,cash};
        if (legal_trade(s,p,offer)) return offer;
    }
    return {};
}
Action record(void* context,const State& s,int p,const Query& q) {
    auto& r=*static_cast<Recorder*>(context);
    if (q.kind==Kind::Offer) {
        auto expert=decide(r.policy,s,p,q);
        if ((expert.give>=0 || expert.take>=0) && r.rng.below(4)!=0) return expert;
        if (r.rng.below(10)==0 && s.turn>=6) return exploratory(s,p,r.rng);
        return {};
    }
    Action a=decide(r.policy,s,p,q);
    if (q.kind==Kind::Accept) {
        if (r.refuses) a={};
        OfferFeatures x=offer_features(s,1-p,{1,-1,q.give,q.take,q.cash});
        float label=a.value==1;
        r.file.write(reinterpret_cast<const char*>(x.data()),sizeof(x));
        r.file.write(reinterpret_cast<const char*>(&label),sizeof(label));
        ++r.offered;
        r.accepted+=label==1;
    }
    return a;
}
}
int main(int argc,char** argv) {
    if (argc!=4) return 2;
    const int games=std::stoi(argv[1]);
    Rng rng{std::stoull(argv[2])};
    std::ofstream file(argv[3],std::ios::binary);
    if (games<1 || !file) return 2;
    int64_t proposals=0,accepted=0,unresolved=0;
    for (int i=0;i<games;++i) {
        auto sample=[&]() {
            Policy p=preset(rng.below(4));
            if (p.style==0) for (double& w:p.w) w=rng.below(1001)/1000.;
            return Recorder{p,Rng{rng.next()},file,rng.below(10)==0};
        };
        Recorder a=sample(), b=sample();
        Policy player=a.policy, rival=b.policy;
        player.external=record; player.context=&a;
        rival.external=record; rival.context=&b;
        Game game(rng.next(),player,rival);
        unresolved+=game.play(1000)<0;
        proposals+=a.offered+b.offered; accepted+=a.accepted+b.accepted;
    }
    file.close();
    if (!file) return 3;
    std::cout<<games<<" "<<proposals<<" "<<accepted<<" "<<unresolved<<"\n";
}
