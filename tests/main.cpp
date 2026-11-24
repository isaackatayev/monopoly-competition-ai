#include "game.hpp"
#include <cassert>
#include <iostream>
void test_board();
void test_economy();
void test_policy();
void test_search();
void test_neural_trade();
void test_neural_policy();
void test_adaptive_policy();
void test_bundle_trade();
void test_robust_policy();

int main() {
    test_board();
    test_economy();
    test_policy();
    test_search();
    test_neural_trade();
    test_neural_policy();
    test_adaptive_policy();
    test_bundle_trade();
    test_robust_policy();
    for (int seed=0;seed<200;++seed) {
        auto a=mono::preset(seed%4), b=mono::preset((seed/4)%4);
        mono::Game game(seed,a,b);
        while (game.s.winner<0 && game.s.turn<350) {
            game.turn();
            const auto& s=game.s;
            if (s.winner>=0) break;
            int houses=s.houses,hotels=s.hotels,counts[2][10]{},mortgages[2]{};
            assert(s.houses>=0 && s.hotels>=0 && s.p[0].cash>=0 && s.p[1].cash>=0);
            for (int i=0;i<40;++i) {
                const int owner=s.owner[i], group=mono::board[i].group, level=s.level[i];
                assert(owner>=-1 && owner<2 && level>=0 && level<=5);
                if (owner>=0) {
                    assert(group>=0);
                    ++counts[owner][group];
                    mortgages[owner]+=s.mort[i];
                }
                if (level) assert(owner>=0 && group>=0 && group<8 && !s.mort[i]);
                houses += level==5 ? 0 : level;
                hotels += level==5;
            }
            assert(houses==32 && hotels==12);
            for (int who=0;who<2;++who) {
                for (int g=0;g<10;++g) assert(counts[who][g]==s.count[who][g]);
                assert(mortgages[who]==s.mort_count[who]);
            }
        }
    }
    std::cout<<"Rule, policy, and game-state invariant tests passed.\n";
}
