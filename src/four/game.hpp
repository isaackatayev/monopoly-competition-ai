#pragma once
#include "../game.hpp"

namespace mono::four {
constexpr int Seats = 4;
enum TradeType { BuyDeed, SellDeed, SwapDeeds, OtherTrade, TradeTypes };
struct Game {
    std::array<Player, Seats> players{};
    std::array<bool, Seats> alive{{true,true,true,true}};
    std::array<int, Squares> owner{}, level{};
    std::array<bool, Squares> mort{};
    int houses=32, hotels=12, active=0, turn=0;
    int initial_seats;
    Rng rng;
    std::array<Deck,2> decks{};
    std::array<Policy,Seats> policies;
    std::array<const double*,Seats> multiplayer_weights{};
    std::array<int,Seats> personal_turns{};
    // Row is the actor. Column identifies the other seat in its bilateral view.
    std::array<std::array<BehaviorHistory,Seats>,Seats> history{};
    std::array<std::array<RejectedOffer,Seats>,Seats> rejected{};
    std::array<std::array<int,Seats>,Seats> offers{}, accepted{};
    std::array<std::array<int,TradeTypes>,Seats> accepted_types{};
    bool trading=true;

    Game(uint64_t seed,const std::array<Policy,Seats>& entrants,int seats=Seats);
    int living() const;
    int winner() const;
    void step();
    int play(int cap);
    void card(int who,int deck);
    void land(int who,int dice,int special=0);
    void return_card(int who,int deck);

    // The asking player is always seat 0 of the duel-policy projection.
    State view(int who,int other) const;
    int focus(int who,const Query&) const;
    Action ask(int who,const Query&,int other=-1) const;
    void observe(int who,const BehaviorEvent&,int partner=-1);
    void trade(int who);

    bool pay(int who,int creditor,int amount);
    void manage(int who);
    void property(int who,int square,int dice,int special=0);
    void transfer(int square,int to);
    void mortgage_election(int who,int square);
    void auction_deed(int square);
};
}
