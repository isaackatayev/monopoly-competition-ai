// Frozen qualification fixtures are shared with the prior release benchmark.
#define main adaptive_bench_main
#include "adaptive_bench.cpp"
#undef main
#include "resilience_arena.cpp"
#include "robust_policy.hpp"
#include "pressure_policy.hpp"
#include "deal_value.hpp"

namespace {
enum class Additive { Development, Liquidity, District };
struct AdditiveProfile {
    Additive family;
    Policy manager;
    double taste;
};

// Two new, non-negotiating opponents. Neither receives the entrant's identity,
// private auction ceiling, dice or deck. Its quote combines a deed price with
// additive dollar premiums; reserves and development differ between families.
Action new_bidder(void* raw,const State& s,int who,const Query& q) {
    auto& r=*static_cast<AdditiveProfile*>(raw);
    if(q.kind==Kind::Offer || q.kind==Kind::Accept) return {};
    if(q.kind!=Kind::Buy && !(q.kind==Kind::Bid && q.amount>=0))
        return decide(r.manager,s,who,q);
    const auto& d=board[q.square];
    int g=d.group, mine=s.count[who][g], theirs=s.count[1-who][g];
    bool finish=mine+1==group_size[g], block=theirs+1==group_size[g];
    double ceiling=0, reserve=0;
    if(r.family==Additive::Liquidity) {
        // Reserve cash for the next roll in proportion to near-term rent.
        double risk=0;
        for(int dice=2;dice<=12;++dice) {
            int dest=(s.p[who].pos+dice)%Squares;
            if(s.owner[dest]==1-who)risk+=(6-std::abs(7-dice))*rent(s,dest,dice)/36.;
        }
        reserve=25+55*r.taste+1.7*risk;
        ceiling=d.mortgage+(.38+.34*r.taste)*d.price+54+68*mine;
        if(g==Rail)ceiling+=55+45*mine;
        if(g<Rail)ceiling+=finish?(240+2.0*d.house):block?(95+1.6*d.house):0;
    } else {
        // Focus on high-return districts, read how many houses the bank holds,
        // and defend against a rival who already controls two related deeds.
        reserve=60+100*r.taste+std::max(0,240-s.p[1-who].cash)/4.;
        double quality=g<Rail?double(d.rents[3])/d.house:0;
        ceiling=.70*d.price+70+38*mine+17*quality;
        if(g==Rail)ceiling+=65+80*mine;
        if(g<Rail && finish)ceiling+=160+quality*(s.houses>=6?58:18);
        if(g<Rail && block)ceiling+=120+d.rents[2]*.75;
    }
    int bid=std::clamp(int(ceiling),0,std::max(0,s.p[who].cash-int(reserve)));
    if(q.kind==Kind::Bid)return {bid};
    return {bid>=d.price && s.p[1-who].cash+1>=d.price};
}

GameResult extra_game(const Policy& actor,const Policy& frozen,Additive family,uint64_t seed,
                      uint64_t conditions,int seat,int cap) {
    Rng random{conditions};
    auto draw=[&](double lo,double hi){return lo+(hi-lo)*random.below(10000)/10000.;};
    Rival market{frozen,{draw(.38,1.),draw(.50,1.),draw(.2,.97),draw(0,.7),draw(0,1.),draw(0,1.)},5};
    AdditiveProfile extra{family,preset(family==Additive::Liquidity?3:2),draw(0,1.)};
    Policy enemy=frozen;
    if(family==Additive::Development) { enemy.external=competing;enemy.context=&market; }
    else {enemy.external=new_bidder;enemy.context=&extra;}
    Game game(seed,seat?enemy:actor,seat?actor:enemy);
    int w=game.play(cap);
    return {w<0?2:int(w!=seat),game.s.turn,game.offers_made[seat],game.offers_closed[seat],
            game.offers_made[1-seat],game.offers_closed[1-seat],0,0,0,0,0,{},{}};
}
}

int main(int argc,char** argv) {
    try {
        if(argc!=10 && argc!=11) throw std::runtime_error("Expected fixture, board seed, first pair, pair count, cap, three networks and fixture seed");
        const std::string name=argv[1];
        bool train=name=="rival_train";
        Fixture f=NumFixtures;
        for(int i=0;i<NumFixtures;++i)if(name==fixture_names[i])f=Fixture(i);
        if(name=="prior_challenger_v1" || name=="prior_challenger_v2" || name=="prior_challenger_v3" || train)f=Learned;
        bool extra=name=="known_additive" || name=="additive_liquidity" || name=="additive_district";
        if(!extra && f==NumFixtures)throw std::runtime_error("Unknown opponent");
        Additive family=name=="known_additive"?Additive::Development:name=="additive_liquidity"?Additive::Liquidity:Additive::District;
        int first=std::stoi(argv[3]), pairs=std::stoi(argv[4]), cap=std::stoi(argv[5]);
        if(first<0 || pairs<1 || cap<1)throw std::runtime_error("Invalid game count");
        NeuralNet value;ResponseNet acceptance;OpponentNet opponent;Specialist spec;RobustWeights weights;
        ResilienceWeights resilience;PressureWeights pressure;
        if(!load_neural(argv[6],value)||!load_response(argv[7],acceptance)||!load_opponent(argv[8],opponent))
            throw std::runtime_error("Model network failed to load");
        Policy frozen=preset(6);frozen.net=&value;frozen.response=&acceptance;
        auto read_weight=[](double& w){if(!(std::cin>>w) || !(w>=0 && w<=1))throw std::runtime_error("Expected weight within [0,1]");};
        for(auto& w:frozen.w)read_weight(w);
        for(auto& w:spec.w)read_weight(w);
        Policy incumbent=frozen;incumbent.style=7;incumbent.specialist=&spec;incumbent.opponent=&opponent;
        Policy candidate=incumbent;
        std::string version;std::cin>>version;
        if(version=="v3" || version=="v4" || version=="pressure") {
            constexpr double lo[]={0,0,0,.5,.5,-150,0,0},hi[]={1,1,1,2,2,150,1,1};
            for(int i=0;i<ResilienceParams;++i) {
                double& w=resilience.w[i];
                if(!(std::cin>>w) || !(w>=lo[i] && w<=hi[i]))throw std::runtime_error("Invalid resilience weight");
            }
            candidate.resilience=&resilience;
            for(auto& w:weights.w)read_weight(w);
            candidate.style=9;candidate.robust=&weights;
            if(version!="v3") {
                for(double& w:pressure.w)if(!(std::cin>>w) || !std::isfinite(w))throw std::runtime_error("Invalid pressure weight");
                candidate.style=version=="pressure"?10:11;
                candidate.pressure=&pressure;
            }
        } else if(version!="v1")throw std::runtime_error("Expected v1 or v3 candidate");
        DealNet deal;
        if(argc==11) {
            if(version=="v1" || !load_deal_net(argv[9],deal))throw std::runtime_error("Invalid candidate trade valuation network");
            candidate.deal_net=&deal;
        }
        std::string reference;std::cin>>reference;
        Policy baseline=incumbent;
        if(reference=="v3_reference") {
            if(candidate.style<10)throw std::runtime_error("A v3 comparison needs the new pressure model");
            baseline=candidate;baseline.style=9;baseline.pressure=nullptr;
        } else if(reference=="without_deal") {
            if(!candidate.deal_net)throw std::runtime_error("Reference ablation requires a trade valuation model");
            baseline=candidate;baseline.deal_net=nullptr;
        } else if(reference!="incumbent")throw std::runtime_error("Unknown reference policy");
        RivalWeights rival{};
        if(f==Learned)for(auto& w:rival)read_weight(w);
        std::string trailing;
        if(std::cin>>trailing)throw std::runtime_error("Unexpected weight after final model parameter");
        uint64_t offset=uint64_t(first)*0x9e3779b97f4a7c15ULL;
        Rng boards{std::stoull(argv[2])+offset},conditions{std::stoull(argv[argc-1])+offset};
        int training[3]{};
        for(int i=first;i<first+pairs;++i) {
            uint64_t gs=boards.next(), cond=conditions.next();
            for(int seat=0;seat<2;++seat) {
                auto match=[&](const Policy& p){
                    return extra ? extra_game(p,frozen,family,gs,cond,seat,cap) :
                        play(p,frozen,f,gs,seat,18+int(cond%36),8+int((cond>>32)%15),cap,false,rival,incumbent);
                };
                if(train) {++training[match(candidate).winner];continue;}
                std::cout<<i<<' '<<seat;
                print(match(baseline));print(match(candidate));std::cout<<'\n';
            }
        }
        if(train)std::cout<<training[1]<<' '<<training[0]<<' '<<training[2]<<'\n';
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
