// Evaluation-only access to the archived league challenger; its bidding and
// management are compiled from the exact fixture used in qualification.
#define main series_unused_fixture_main
#include "adaptive_bench.cpp"
#undef main
#include "evaluation/policy.hpp"

extern "C" size_t mono_series_config_size() { return sizeof(mono::evaluation::EvalConfig); }

extern "C" void mono_series_outcomes(mono::evaluation::EvalConfig actor_config,
        mono::evaluation::EvalConfig frozen_config, const double* rival_weights,
        const uint64_t* board_seeds,const uint8_t* seats,int n,int cap,uint8_t* outcomes) {
    const auto actor = mono::evaluation::entrant(actor_config);
    const auto frozen = mono::evaluation::entrant(frozen_config);
    RivalWeights weights;
    std::copy_n(rival_weights,weights.size(),weights.begin());
    Challenger context{frozen,weights};
    Policy rival = frozen;
    rival.external = learned_rival;
    rival.context = &context;
    for (int i = 0; i < n; ++i) {
        const int seat = seats[i];
        Game game(board_seeds[i],seat ? rival : actor,seat ? actor : rival);
        const int winner = game.play(cap);
        outcomes[i] = winner == seat ? 0 : winner < 0 ? 2 : 1;
    }
}
