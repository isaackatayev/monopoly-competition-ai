#include "four/game.hpp"
#include <initializer_list>
#include <stdexcept>

namespace {
using Game = mono::four::Game;
using mono::four::Seats;

void check(bool value, const char* label) {
    if (!value) throw std::runtime_error(label);
}

mono::Action pass(void*, const mono::State&, int, const mono::Query&) { return {}; }

Game table(int seats=Seats) {
    std::array<mono::Policy,Seats> policies{};
    for (auto& policy : policies) policy.external = pass;
    return Game(11, policies, seats);
}

void stack_card(mono::Deck& deck, int id) {
    for (int i = 0; i < deck.size; ++i) {
        auto& candidate = deck.ids[(deck.head + i) % 16];
        if (candidate == id) {
            std::swap(deck.ids[deck.head], candidate);
            return;
        }
    }
    throw std::runtime_error("Four-seat fixture card absent from deck");
}

void dice(Game& game, std::initializer_list<int> rolls) {
    for (uint64_t seed = 0; seed < 1000000; ++seed) {
        mono::Rng rng{seed};
        if (std::all_of(rolls.begin(), rolls.end(), [&](int n) { return rng.below(6) + 1 == n; })) {
            game.rng.s = seed;
            return;
        }
    }
    throw std::runtime_error("Four-seat fixture dice unavailable");
}

std::array<int,Seats> balances(const Game& game) {
    std::array<int,Seats> result;
    for (int seat = 0; seat < Seats; ++seat) result[seat] = game.players[seat].cash;
    return result;
}

void check_inventory(const Game& game) {
    int houses = game.houses, hotels = game.hotels;
    for (int sq = 0; sq < mono::Squares; ++sq) {
        const int owner = game.owner[sq];
        check(owner == -1 || (owner >= 0 && owner < Seats && game.alive[owner]),
              "Four-seat deed owner is still in the game");
        const int level = game.level[sq];
        check(level >= 0 && level <= 5, "Valid four-seat building level");
        if (level == 5) ++hotels;
        else houses += level;
    }
    check(houses == 32 && hotels == 12, "Shared building stock is conserved");
    for (int d = 0; d < 2; ++d) {
        std::array<int,16> copies{};
        const auto& deck = game.decks[d];
        for (int i=0; i<deck.size; ++i) ++copies.at(deck.ids[(deck.head+i)%16]);
        for (const auto& p : game.players) copies[d == 0 ? 7 : 4] += !!(p.cards & (1u << d));
        check(std::all_of(copies.begin(), copies.end(), [](int n) { return n == 1; }),
              "Every card is either in the deck or held by exactly one player");
    }
}
}

void test_four_board() {
    {
        auto game = table(3);
        check(game.initial_seats == 3 && game.living() == 3 && !game.alive[3] &&
              balances(game) == std::array<int,Seats>{1500,1500,1500,0}, "Three players enter with cash");
        stack_card(game.decks[1], 8);
        game.card(1, 1);
        check(balances(game) == std::array<int,Seats>{1490,1520,1490,0},
              "Three-player Birthday collects from exactly two opponents");
        game.active = 2;
        dice(game, {1,3});
        game.step();
        check(game.active == 0 && game.personal_turns[3] == 0,
              "Clockwise three-player rotation skips the unused slot");
        check(!game.pay(0, 1, 5000) && game.living() == 2 && game.winner() < 0,
              "Three-player game continues after its first elimination");
        check(!game.pay(1, 2, 5000) && game.winner() == 2, "Second elimination ends three-player game");
        check_inventory(game);
    }
    {
        auto game = table();
        check(game.winner() == -1 && game.living() == Seats, "Four players start alive");
        check(balances(game) == std::array<int,Seats>{1500,1500,1500,1500}, "Four starting balances");
        check(game.play(0) == -1 && game.turn == 0, "Capped table stays unresolved");
        stack_card(game.decks[1], 8); // Birthday
        game.card(2, 1);
        check(balances(game) == std::array<int,Seats>{1490,1490,1530,1490}, "Collect from three seats");
        stack_card(game.decks[0], 14); // Chairman
        game.card(2, 0);
        check(balances(game) == std::array<int,Seats>{1540,1540,1380,1540}, "Pay three seats");
    }
    {
        auto game = table();
        game.players[2].cash = 65;
        stack_card(game.decks[0], 14);
        game.card(2, 0);
        check(!game.alive[2] && game.living() == 3 && game.winner() == -1,
              "First bankruptcy leaves a three-player game");
        check(balances(game) == std::array<int,Seats>{1515,1500,0,1550},
              "Pay clockwise and stop when the drawer goes bankrupt");
        game.players[3].cash = 3;
        stack_card(game.decks[1], 8);
        game.card(1, 1);
        check(!game.alive[3] && game.living() == 2, "Birthday can eliminate a payer");
        check(balances(game) == std::array<int,Seats>{1505,1513,0,0},
              "Birthday skips bankrupt seats and keeps collecting from remaining payers");
        game.players[0].cash = 5;
        stack_card(game.decks[1], 8);
        game.card(1, 1);
        check(game.winner() == 1 && game.play(100) == 1 && game.turn == 0,
              "Final bankruptcy ends the four-seat match immediately");
    }
    {
        auto game = table();
        stack_card(game.decks[0], 7);
        game.card(3, 0);
        stack_card(game.decks[1], 4);
        game.card(0, 1);
        check(game.players[3].cards == 1 && game.players[0].cards == 2
              && game.decks[0].size == 15 && game.decks[1].size == 15,
              "Held cards are unique across four players");
        game.return_card(3, 0);
        const auto& deck = game.decks[0];
        check(deck.size == 16 && deck.ids[(deck.head + 15) % 16] == 7 &&
              game.players[3].cards == 0 && game.players[0].cards == 2,
              "Release card goes to the bottom of its own shared deck");
    }
    {
        auto game = table();
        game.trading = false;
        for (int seat = 0; seat < Seats; ++seat) {
            check(game.active == seat, "Opening turn rotates clockwise");
            dice(game, {1,3}); // Income Tax
            game.step();
        }
        check(game.active == 0 && game.turn == 4 &&
              game.personal_turns == std::array<int,Seats>{1,1,1,1}, "Each seat gets one turn per lap");
        game.active = 2;
        game.alive[1] = false;
        game.alive[3] = false;
        game.players[2].pos = 18;
        game.transfer(24, 2);
        dice(game, {1,1,2,2,3,3});
        game.step();
        check(game.players[2].pos == 10 && game.players[2].jail == 0 && game.active == 0 &&
              game.personal_turns[2] == 2, "Third double sends player to jail before skipping dead seat");
        game.active = 2;
        dice(game, {5,5});
        game.step();
        check(game.players[2].jail == -1 && game.players[2].pos == 20 && game.active == 0,
              "Doubles release a prisoner without an extra roll");
        game.active = 2;
        game.players[2].jail = 2;
        game.players[2].pos = 10;
        game.transfer(13, 2);
        dice(game, {1,2});
        game.step();
        check(game.players[2].jail == -1 && game.players[2].pos == 13 && game.players[2].cash == 1250,
              "Third failed jail attempt charges the active player and moves them");
    }
    for (uint64_t seed = 0; seed < 8; ++seed) {
        std::array<mono::Policy,Seats> lineup;
        for (int seat = 0; seat < Seats; ++seat) lineup[seat] = mono::preset(1 + (seed + seat) % 4);
        Game game(seed, lineup), replay(seed, lineup);
        do {
            game.step();
            check_inventory(game);
        } while (game.living() > 1 && game.turn < 240);
        check(replay.play(240) == game.winner() && replay.rng.s == game.rng.s &&
              replay.owner == game.owner && replay.level == game.level && replay.turn == game.turn &&
              balances(replay) == balances(game) && replay.accepted == game.accepted,
              "Four-player fixture replays with the same seed and seating");
    }
}
