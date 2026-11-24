#include "game.hpp"

#include <stdexcept>

using namespace mono;
namespace {
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
}

void test_search() {
    Policy policy = preset(0);
    // A turn-14 position sampled from a game. The offer asks seat 0 to pay $125 and
    // trade B. & O. Railroad for Illinois Avenue, giving it its second red deed.
    auto state = Game(11, policy, policy).s;
    state.turn = 14;
    state.active = 1;
    state.p[0] = {758, 20, -1, 0};
    state.p[1] = {902, 1, -1, 0};
    for (int square : {6, 8, 14, 23, 25, 29}) {
        state.owner[square] = 0;
        ++state.count[0][board[square].group];
    }
    for (int square : {1, 18, 24, 32}) {
        state.owner[square] = 1;
        ++state.count[1][board[square].group];
    }
    Query pending{Kind::Accept, -1, 0, 24, 25, -125};
    require(decide(policy, state, 0, pending).value == 1, "Static policy accepts the red-deed fixture");
    require(search_accept(policy, state, 0, pending).value == 0, "Trade rollouts decline the fixture");

    // Both held cards are public. The actual game's hidden decks differ, and the
    // policy must give the same action from either copy of the public position.
    state.p[0].cards = 1;
    state.p[1].cards = 2;
    Game first(1101, policy, policy), second(9902, policy, policy);
    first.s = second.s = state;
    const auto choice = search_accept(policy, first.s, 0, pending);
    require(choice.value == search_accept(policy, second.s, 0, pending).value,
            "Trade rollouts depend only on public information");
    require(first.s.p[0].cards == 1 && first.s.p[1].cards == 2 && first.s.turn == 14 &&
            first.s.p[0].cash == 758 && first.s.owner[25] == 0, "Search preserves the position");

    state.turn = 1000;
    require(search_accept(policy, state, 0, pending).value == decide(policy, state, 0, pending).value,
            "Rollout horizon uses base policy at the game cap");
}
