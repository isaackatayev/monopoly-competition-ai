#include "game.hpp"
#include <algorithm>
#include <initializer_list>
#include <stdexcept>

using namespace mono;
namespace {
void check(bool value, const char* label) {
    if (!value) throw std::runtime_error(label);
}
Action passive(void* ctx, const State&, int, const Query& q) {
    return q.kind == Kind::Jail ? Action{*static_cast<int*>(ctx)} : Action{};
}
void dice(Game& game, std::initializer_list<int> sequence) {
    for (uint64_t seed = 0; seed < 1000000; ++seed) {
        Rng rng{seed};
        if (std::all_of(sequence.begin(), sequence.end(), [&](int n){ return rng.below(6) + 1 == n; })) {
            game.rng.s = seed;
            return;
        }
    }
    throw std::runtime_error("Unable to construct test rolls");
}
void stack(Deck& deck, int id) {
    for (int i = 0; i < deck.size; ++i) {
        int index = (deck.head + i) % 16;
        if (deck.ids[index] == id) {
            std::swap(deck.ids[deck.head], deck.ids[index]);
            return;
        }
    }
    throw std::runtime_error("Test card missing");
}
void assign(Game& game, int who, int square) {
    game.s.owner[square] = static_cast<int8_t>(who);
    ++game.s.count[who][board[square].group];
}
void check_cards(const Game& game) {
    const State& s = game.s;
    for (int d = 0; d < 2; ++d) {
        const Deck& deck = game.decks[d];
        int copies[16]{};
        int held = !!(s.p[0].cards & (1u << d)) + !!(s.p[1].cards & (1u << d));
        check(held + deck.size == 16, "Release card conservation");
        copies[d ? 4 : 7] = held;
        for (int i = 0; i < deck.size; ++i) ++copies[deck.ids[(deck.head + i) % 16]];
        for (int n : copies) check(n == 1, "Every card exists exactly once");
    }
}
}

void test_board() {
    int jail_choice = 0;
    Policy passive_player;
    passive_player.external = passive;
    passive_player.context = &jail_choice;
    {
        Game game(1, passive_player, passive_player);
        std::array<int,Groups> counts{};
        for (int i = 0; i < Squares; ++i) {
            check(game.s.owner[i] == -1, "Initial deed ownership");
            if (board[i].group >= 0) ++counts[board[i].group];
        }
        check(counts == group_size, "Deed group sizes");
        for (const auto& deck : game.decks) {
            auto ids = deck.ids;
            std::sort(ids.begin(), ids.end());
            for (int i = 0; i < 16; ++i) check(ids[i] == i, "Deck is a permutation");
        }
    }
    {
        Game game(2, passive_player, passive_player);
        game.s.p[0].pos = 36;
        stack(game.decks[0], 8); // Back three onto Chest at 33
        stack(game.decks[1], 1); // Bank error
        game.land(0, 6);
        check(game.s.p[0].pos == 33 && game.s.p[0].cash == 1700, "Chance chains to Chest");
        stack(game.decks[0], 7);
        stack(game.decks[1], 4);
        game.card(0, 0);
        game.card(0, 1);
        check(game.s.p[0].cards == 3 && game.decks[0].size == 15 && game.decks[1].size == 15,
              "Remove both held release cards");
        game.return_card(0, 0);
        check(game.s.p[0].cards == 2 && game.decks[0].size == 16, "Release one held card");
        check(game.decks[0].ids[(game.decks[0].head + 15) % 16] == 7, "Return card to bottom");
    }
    {
        Game game(3, passive_player, passive_player);
        assign(game, 1, 5);
        game.s.p[0].pos = 36;
        stack(game.decks[0], 4);
        game.land(0, 8);
        check(game.s.p[0].pos == 5 && game.s.p[0].cash == 1650 && game.s.p[1].cash == 1550,
              "Nearest railroad wraps through GO and doubles rent");
        assign(game, 1, 12);
        game.s.p[0].pos = 7;
        stack(game.decks[0], 3);
        dice(game, {2,3});
        game.land(0, 7);
        check(game.s.p[0].pos == 12 && game.s.p[0].cash == 1600, "Chance utility uses 10 times fresh roll");
        dice(game, {1,2});
        game.land(0, 8);
        check(game.s.p[0].cash == 1588, "Normal utility uses four times fresh roll");
    }
    {
        Game game(4, passive_player, passive_player);
        game.s.p[0].pos = 18;
        assign(game, 0, 24);
        dice(game, {1,1,2,2,3,3});
        game.turn();
        check(game.s.p[0].pos == 10 && game.s.p[0].jail == 0 && game.s.turn == 1,
              "Third double ends a single turn in jail");
        game.s.active = 0;
        dice(game, {3,3});
        game.turn();
        check(game.s.p[0].pos == 16 && game.s.p[0].jail == -1, "Doubles to leave jail end turn");
        game.s.active = 0;
        game.s.p[0].pos = 10;
        game.s.p[0].jail = 2;
        dice(game, {1,2});
        game.turn();
        check(game.s.p[0].pos == 13 && game.s.p[0].jail == -1 && game.s.p[0].cash == 1450,
              "Third failed jail roll costs 50 and moves");
    }
    {
        Game game(5, passive_player, passive_player);
        game.s.p[0].pos = 24;
        dice(game, {3,3});
        game.turn();
        check(game.s.p[0].pos == 10 && game.s.p[0].jail == 0 && game.s.p[0].cash == 1500,
              "Go To Jail ends double without salary");
        stack(game.decks[1], 4);
        game.card(0, 1);
        game.s.active = 0;
        jail_choice = 3;
        dice(game, {1,2});
        game.turn();
        check(game.s.p[0].cards == 0 && game.s.p[0].pos == 13 && game.s.p[0].cash == 1500,
              "Release card lets player move normally");
    }
    for (int seed = 0; seed < 96; ++seed) {
        Policy first = preset(seed % 4), second = preset((seed / 4) % 4);
        Game game(seed, first, second), replay(seed, first, second);
        do {
            check_cards(game);
            game.turn();
        } while (game.s.winner < 0 && game.s.turn < 240);
        check(replay.play(240) == game.s.winner && replay.s.turn == game.s.turn &&
              replay.rng.s == game.rng.s && replay.s.owner == game.s.owner &&
              replay.s.p[0].cash == game.s.p[0].cash && replay.s.p[1].cash == game.s.p[1].cash,
              "Same seed and policies replay identically");
    }
}
