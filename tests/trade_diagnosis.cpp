// Controlled changes to the incumbent's outgoing offers. Every other decision,
// including evaluating incoming offers, uses its original deployed policy.
#define main adaptive_bench_main
#include "../src/adaptive_bench.cpp"
#undef main

namespace {
struct Filter {Policy baseline;int mode;};
Action filter(void* context,const State& s,int who,const Query& q) {
    const auto& f=*static_cast<const Filter*>(context);
    auto action=decide(f.baseline,s,who,q);
    if(q.kind!=Kind::Offer || !action.value)return action;
    if(f.mode==1)return {};
    if(f.mode==2 && action.give>=0) {
        int give=board[action.give].group,take=action.take<0?-1:board[action.take].group;
        if(give<Rail && give!=take && s.count[1-who][give]+1==group_size[give])return {};
    }
    return action;
}
}
int main(int argc,char**argv) {
    if(argc!=9)return 1;
    const std::string name=argv[1];
    int games=std::stoi(argv[2]),mode=std::stoi(argv[3]);
    Rng rng{std::stoull(argv[4])};
    int first=std::stoi(argv[5]);
    if(games<2 || games%2 || mode<0 || mode>2 || first<0)return 1;
    rng.s+=uint64_t(first)*0x9e3779b97f4a7c15ULL;
    NeuralNet value;ResponseNet response;OpponentNet opponent;Specialist spec;
    if(!load_neural(argv[6],value)||!load_response(argv[7],response)||!load_opponent(argv[8],opponent))return 1;
    Policy frozen=preset(6);frozen.net=&value;frozen.response=&response;
    for(auto& w:frozen.w)if(!(std::cin>>w))return 1;
    for(auto& w:spec.w)if(!(std::cin>>w))return 1;
    Policy baseline=frozen;baseline.style=7;baseline.opponent=&opponent;baseline.specialist=&spec;
    RivalWeights rival{};
    Fixture fixture=NumFixtures;
    for(int i=0;i<NumFixtures;++i)if(name==fixture_names[i])fixture=Fixture(i);
    if(fixture==NumFixtures)return 1;
    if(fixture==Learned)for(auto& w:rival)if(!(std::cin>>w))return 1;
    Filter ctx{baseline,mode};Policy actor=baseline;actor.external=filter;actor.context=&ctx;
    for(int i=0;i<games/2;++i) {
        auto seed=rng.next();
        for(int seat=0;seat<2;++seat) {
            auto result=play(actor,frozen,fixture,seed,seat,0,0,1000,false,rival,baseline);
            std::cout<<first+i<<' '<<seat<<' '<<result.winner<<' '<<result.offered<<' '<<result.closed<<'\n';
        }
    }
}
