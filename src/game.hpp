#pragma once
#include <algorithm>
#include <array>
#include <cstdint>

namespace mono {
constexpr int Squares = 40, Groups = 10, Params = 12;
constexpr int Rail = 8, Utility = 9;
struct Space {
    const char* name;
    int group;                  // -1 non-deed, 0..7 colors, 8 railroad, 9 utility
    int price, mortgage, house;
    std::array<int,6> rents;    // base, 1..4 houses, hotel; special groups handled separately
};
extern const std::array<Space,Squares> board;
extern const std::array<int,Groups> group_size;
struct Player {
    int cash = 1500, pos = 0, jail = -1; // jailed: 0..2 failed attempts
    unsigned cards = 0;                  // bit 0 Chance, bit 1 Chest
};
struct RejectedOffer { int give=-1, take=-1, cash=0, turn=0; };
enum BehaviorKind {
    DeclinedTrade, AcceptedTrade, ProposedTrade, BoughtAtFace, DeclinedFace, WonAuction,
    DeclinedBundle, AcceptedBundle, ProposedBundle
};
struct BehaviorEvent { int kind=0, square=-1, amount=0, turn=0, give=-1, take=-1, generosity=0; };
struct BehaviorHistory {
    std::array<BehaviorEvent,16> events{};
    int size=0;
    void append(BehaviorEvent event) {
        if (size < static_cast<int>(events.size())) { events[size++] = event; return; }
        std::move(events.begin()+1, events.end(), events.begin());
        events.back() = event;
    }
};
struct State {
    std::array<BehaviorHistory,2> behavior{};
    std::array<RejectedOffer,2> rejected{};
    Player p[2];
    std::array<int8_t,Squares> owner{}, level{};   // owner initialized to -1 by run
    std::array<bool,Squares> mort{};
    int count[2][Groups]{};
    int mort_count[2]{};
    int houses = 32, hotels = 12, turn = 0, active = 0, winner = -1;
};
struct Rng {
    uint64_t s;
    uint64_t next() {
        uint64_t z = (s += 0x9e3779b97f4a7c15ULL);
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
        return z ^ (z >> 31);
    }
    int below(int n) { return static_cast<int>((static_cast<__uint128_t>(next()) * static_cast<unsigned>(n)) >> 64); }
};
struct Deck { std::array<int,16> ids{}; int head=0, size=16; };
enum class Kind { Buy, Bid, Manage, Liquidate, Jail, Offer, Accept };
struct Query { Kind kind; int square=-1, amount=0, give=-1, take=-1, cash=0; };
// Manage / Liquidate: action type with a square. Offer: give/take optional single
// deeds from the proposer's perspective, cash signed from proposer to recipient.
// Jail: value 0 roll, 1 pay, 2 use Chance card, 3 use Chest card.
// Buy / Accept: value 0 or 1. Bid: value = maximum cash bid.
enum Act { Pass, Build, Sell, Mortgage, Redeem };
struct Action { int value=0, square=-1, give=-1, take=-1, cash=0; };
enum class TableEvent { BeforeRoll, Dice, Landing, Card, Rent };
using TableObserver = void (*)(void*, const State&,TableEvent,int,int,int);
using External = Action (*)(void*, const State&,int,const Query&);
struct NeuralNet;
struct ResponseNet;
struct DealNet;
struct OpponentNet;
struct Specialist;
struct ResilienceWeights;
struct RobustWeights;
struct PressureWeights;
struct Policy {
    int style=0;                            // 0 trainable; 1..3 training opponents, 4 held-out blocker
    std::array<double,Params> w{};          // all parameters normalized to [0,1]
    External external=nullptr;
    void* context=nullptr;
    const NeuralNet* net=nullptr;
    const ResponseNet* response=nullptr;
    const OpponentNet* opponent=nullptr;
    const Specialist* specialist=nullptr;
    const ResilienceWeights* resilience=nullptr;
    const RobustWeights* robust=nullptr;
    const DealNet* deal_net=nullptr;
    const PressureWeights* pressure=nullptr;
};
extern const std::array<const char*,Params> param_names;
Policy preset(int style);
Action adaptive_decide(const Policy&,const State&,int,const Query&);
Action pressure_decide(const Policy&,const State&,int,const Query&);
int offer_generosity(const State&,int proposer,const Action&);
Action resilient_decide(const Policy&,const State&,int,const Query&);
Action neural_decide(const Policy&,const State&,int,const Query&);
Action holdout(const State&,int player,const Query&);
Action search_accept(const Policy& base,const State&,int player,const Query&);
Action decide(const Policy&,const State&,int player,const Query&);
inline Action ask(const Policy& pol,const State& s,int player,const Query& q) {
    return pol.external ? pol.external(pol.context,s,player,q) : decide(pol,s,player,q);
}
struct Game {
    State s;
    Rng rng;
    Deck decks[2];
    const Policy* policies[2];
    int offers_made[2]{}, offers_closed[2]{};
    void (*trade_window)(Game&,int)=nullptr;
    void* trade_window_context=nullptr;
    TableObserver table_observer=nullptr;
    void* table_context=nullptr;
    void observe(TableEvent event,int who,int first=0,int second=0) const {
        if (table_observer) table_observer(table_context,s,event,who,first,second);
    }
    Game(uint64_t seed,const Policy& a,const Policy& b);
    Action ask_player(int player,const Query& q) const { return ask(*policies[player],s,player,q); }
    // board.cpp
    void turn();
    void land(int who,int dice,int special=0); // special 1 double railroad rent, 2 10x fresh utility roll
    void card(int who,int deck);
    void return_card(int who,int deck);
    int play(int cap);                        // returns -1 for unresolved, else winning seat
    // economy.cpp
    void property(int who,int square,int dice,int special);
    bool pay(int who,int creditor,int amount); // creditor -1 = bank
    void manage(int who);
    void trade(int who);
};
bool can_build(const State&,int who,int square);
bool can_sell(const State&,int who,int square);
bool can_mortgage(const State&,int who,int square);
bool can_redeem(const State&,int who,int square);
int rent(const State&,int square,int dice,int special=0);
bool legal_trade(const State&,int proposer,const Action&);
void condition_decks_on_held_cards(Game&);
void settle_trade(State&,int proposer,const Action&,const Policy& proposer_policy,const Policy& recipient_policy);
double trade_deed_value(const Policy&,const State&,int seat,int square,bool gaining);
} // namespace mono
