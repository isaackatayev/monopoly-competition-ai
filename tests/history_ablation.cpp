// Import the frozen qualification fixtures and seat runner without changing their executable.
// The imported main normally has an implicit return of zero. It is never called under its test name.
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wreturn-type"
#endif
#define main qualification_program_main
#include "../src/adaptive_bench.cpp"
#undef main
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

namespace {
Action hide_history(void* context,const State& state,int who,const Query& query) {
    const Policy& entrant=*static_cast<const Policy*>(context);
    if (state.behavior[0].size==0 && state.behavior[1].size==0)
        return decide(entrant,state,who,query);
    State visible=state;
    // Both tails can reveal rival prices. Keep rejected offers and all other v1 board fields.
    visible.behavior={};
    return decide(entrant,visible,who,query);
}
}

int main(int argc,char** argv) {
    try {
        if(argc!=10) throw std::runtime_error("Expected fixture, board seed, pair range, cap, models and regime seed");
        Fixture fixture=NumFixtures;
        for(int i=0;i<NumFixtures;++i) if(fixture_names[i]==std::string(argv[1])) fixture=Fixture(i);
        const int start=std::stoi(argv[3]), pairs=std::stoi(argv[4]), cap=std::stoi(argv[5]);
        if(fixture==NumFixtures || start<0 || pairs<1 || cap<1) throw std::runtime_error("Invalid game configuration");
        NeuralNet value;
        ResponseNet response;
        OpponentNet opponent;
        Specialist specialist;
        if(!load_neural(argv[6],value) || !load_response(argv[7],response) || !load_opponent(argv[8],opponent))
            throw std::runtime_error("Failed loading verified network");
        Policy fixed=preset(0);
        for(auto& w:fixed.w) if(!(std::cin>>w)) throw std::runtime_error("Missing prior weights");
        for(auto& w:specialist.w) if(!(std::cin>>w)) throw std::runtime_error("Missing specialist weights");
        fixed.style=6; fixed.net=&value; fixed.response=&response;
        Policy full=fixed;
        full.style=7; full.opponent=&opponent; full.specialist=&specialist;
        const Policy incumbent=full;
        ResilienceWeights resilience;
        read_resilience(full,resilience);
        RivalWeights rival{};
        if(fixture==Learned) for(auto& w:rival) if(!(std::cin>>w) || !(w>=0 && w<=1)) throw std::runtime_error("Invalid rival weights");
        Policy masked=full;
        masked.external=hide_history;
        masked.context=&full;
        Rng boards{std::stoull(argv[2])+uint64_t(start)*0x9e3779b97f4a7c15ULL};
        Rng regimes{std::stoull(argv[9])+uint64_t(start)*0x9e3779b97f4a7c15ULL};
        for(int pair=start;pair<start+pairs;++pair) {
            const auto seed=boards.next(), transition=regimes.next();
            const int threshold=18+int(transition%36), cooldown=8+int((transition>>32)%15);
            for(int seat=0;seat<2;++seat) {
                std::cout<<pair<<' '<<seat;
                print(play(masked,fixed,fixture,seed,seat,threshold,cooldown,cap,false,rival,incumbent));
                print(play(full,fixed,fixture,seed,seat,threshold,cooldown,cap,false,rival,incumbent));
                std::cout<<'\n';
            }
        }
    } catch (const std::exception& error) {
        std::cerr<<error.what()<<'\n';
        return 1;
    }
}
