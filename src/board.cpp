#include "game.hpp"

namespace mono {

namespace {
constexpr Space stop(const char* name) {
    return {name, -1, 0, 0, 0, {}};
}
constexpr Space deed(const char* name, int group, int price, int house,
                     std::array<int,6> rents) {
    return {name, group, price, price / 2, house, rents};
}
constexpr Space rail(const char* name) { return deed(name, Rail, 200, 0, {}); }
constexpr Space utility(const char* name) { return deed(name, Utility, 150, 0, {}); }

enum Chance {
    ToGo, ToIllinois, ToStCharles, ToUtility, ToRailA, ToRailB, Dividend,
    ChanceRelease, BackThree, ChanceJail, GeneralRepairs, Speeding, ToReading,
    ToBoardwalk, Chairman, Loan
};
enum Chest {
    ChestGo, BankError, Doctor, Stock, ChestRelease, ChestJail, Holiday, Refund,
    Birthday, Insurance, Hospital, School, Consultancy, StreetRepairs, Prize, Inherit
};
constexpr int release_card[] = {ChanceRelease, ChestRelease};

int roll_die(Game& g) {
    return 1 + g.rng.below(6);
}
void send_to_jail(Player& p) {
    p.pos = 10;
    p.jail = 0;
}
void advance(Game& g, int who, int target, int dice, int special = 0) {
    Player& p = g.s.p[who];
    if (target < p.pos) p.cash += 200;
    p.pos = target;
    g.land(who, dice, special);
}
void put_back(Deck& deck, int id) {
    deck.ids[(deck.head + deck.size) % 16] = id;
    ++deck.size;
}
int repair_bill(const State& s, int who, int per_house, int per_hotel) {
    int bill = 0;
    for (int i = 0; i < Squares; ++i) {
        if (s.owner[i] == who) {
            bill += s.level[i] == 5 ? per_hotel : s.level[i] * per_house;
        }
    }
    return bill;
}
}

const std::array<Space,Squares> board = {{
    stop("GO"),
    deed("Mediterranean Avenue", 0, 60, 50, {2,10,30,90,160,250}),
    stop("Community Chest"),
    deed("Baltic Avenue", 0, 60, 50, {4,20,60,180,320,450}),
    stop("Income Tax"),
    rail("Reading Railroad"),
    deed("Oriental Avenue", 1, 100, 50, {6,30,90,270,400,550}),
    stop("Chance"),
    deed("Vermont Avenue", 1, 100, 50, {6,30,90,270,400,550}),
    deed("Connecticut Avenue", 1, 120, 50, {8,40,100,300,450,600}),
    stop("Jail / Just Visiting"),
    deed("St. Charles Place", 2, 140, 100, {10,50,150,450,625,750}),
    utility("Electric Company"),
    deed("States Avenue", 2, 140, 100, {10,50,150,450,625,750}),
    deed("Virginia Avenue", 2, 160, 100, {12,60,180,500,700,900}),
    rail("Pennsylvania Railroad"),
    deed("St. James Place", 3, 180, 100, {14,70,200,550,750,950}),
    stop("Community Chest"),
    deed("Tennessee Avenue", 3, 180, 100, {14,70,200,550,750,950}),
    deed("New York Avenue", 3, 200, 100, {16,80,220,600,800,1000}),
    stop("Free Parking"),
    deed("Kentucky Avenue", 4, 220, 150, {18,90,250,700,875,1050}),
    stop("Chance"),
    deed("Indiana Avenue", 4, 220, 150, {18,90,250,700,875,1050}),
    deed("Illinois Avenue", 4, 240, 150, {20,100,300,750,925,1100}),
    rail("B. & O. Railroad"),
    deed("Atlantic Avenue", 5, 260, 150, {22,110,330,800,975,1150}),
    deed("Ventnor Avenue", 5, 260, 150, {22,110,330,800,975,1150}),
    utility("Water Works"),
    deed("Marvin Gardens", 5, 280, 150, {24,120,360,850,1025,1200}),
    stop("Go To Jail"),
    deed("Pacific Avenue", 6, 300, 200, {26,130,390,900,1100,1275}),
    deed("North Carolina Avenue", 6, 300, 200, {26,130,390,900,1100,1275}),
    stop("Community Chest"),
    deed("Pennsylvania Avenue", 6, 320, 200, {28,150,450,1000,1200,1400}),
    rail("Short Line"),
    stop("Chance"),
    deed("Park Place", 7, 350, 200, {35,175,500,1100,1300,1500}),
    stop("Luxury Tax"),
    deed("Boardwalk", 7, 400, 200, {50,200,600,1400,1700,2000})
}};
const std::array<int,Groups> group_size = {2,3,3,3,3,3,3,2,4,2};

Game::Game(uint64_t seed, const Policy& a, const Policy& b)
    : rng{seed}, policies{&a, &b} {
    s.owner.fill(-1);
    for (Deck& deck : decks) {
        for (int i = 0; i < 16; ++i) deck.ids[i] = i;
        for (int i = 15; i > 0; --i) {
            std::swap(deck.ids[i], deck.ids[rng.below(i + 1)]);
        }
    }
}

void Game::return_card(int who, int deck) {
    const unsigned bit = 1u << deck;
    if (s.p[who].cards & bit) {
        s.p[who].cards &= ~bit;
        put_back(decks[deck], release_card[deck]);
    }
}

void Game::card(int who, int deck) {
    Deck& pile = decks[deck];
    Player& p = s.p[who];
    int id = pile.ids[pile.head];
    pile.head = (pile.head + 1) % 16;
    --pile.size;
    observe(TableEvent::Card, who, deck, id);
    if (id == release_card[deck]) {
        p.cards |= 1u << deck;
        return;
    }
    put_back(pile, id);

    if (deck == 0) {
        switch (id) {
        case ToGo:       advance(*this, who, 0, 0); break;
        case ToIllinois: advance(*this, who, 24, 0); break;
        case ToStCharles:advance(*this, who, 11, 0); break;
        case ToUtility:  advance(*this, who, p.pos < 12 || p.pos >= 28 ? 12 : 28, 0, 2); break;
        case ToRailA:
        case ToRailB:    advance(*this, who, ((p.pos + 5) / 10 * 10 + 5) % Squares, 0, 1); break;
        case Dividend:   p.cash += 50; break;
        case BackThree:  p.pos -= 3; land(who, 0); break;
        case ChanceJail: send_to_jail(p); break;
        case GeneralRepairs: pay(who, -1, repair_bill(s, who, 25, 100)); break;
        case Speeding:   pay(who, -1, 15); break;
        case ToReading:  advance(*this, who, 5, 0); break;
        case ToBoardwalk:advance(*this, who, 39, 0); break;
        case Chairman:   pay(who, 1 - who, 50); break;
        case Loan:       p.cash += 150; break;
        }
        return;
    }
    switch (id) {
    case ChestGo:       advance(*this, who, 0, 0); break;
    case BankError:     p.cash += 200; break;
    case Doctor:        pay(who, -1, 50); break;
    case Stock:         p.cash += 50; break;
    case ChestJail:     send_to_jail(p); break;
    case Holiday:       p.cash += 100; break;
    case Refund:        p.cash += 20; break;
    case Birthday:      pay(1 - who, who, 10); break;
    case Insurance:     p.cash += 100; break;
    case Hospital:      pay(who, -1, 100); break;
    case School:        pay(who, -1, 50); break;
    case Consultancy:   p.cash += 25; break;
    case StreetRepairs: pay(who, -1, repair_bill(s, who, 40, 115)); break;
    case Prize:         p.cash += 10; break;
    case Inherit:       p.cash += 100; break;
    }
}

void Game::land(int who, int dice, int special) {
    int square = s.p[who].pos;
    observe(TableEvent::Landing, who, square);
    if (board[square].group >= 0) {
        // C1009 instructs the visitor to roll again to set utility rent.
        if (board[square].group == Utility && s.owner[square] == 1 - who && !s.mort[square]) {
            int first = roll_die(*this);
            dice = first + roll_die(*this);
        }
        property(who, square, dice, special);
        return;
    }
    switch (square) {
    case 2: case 17: case 33: card(who, 1); break;
    case 7: case 22: case 36: card(who, 0); break;
    case 4:  pay(who, -1, 200); break;
    case 38: pay(who, -1, 100); break;
    case 30: send_to_jail(s.p[who]); break;
    }
}

void Game::turn() {
    int who = s.active;
    Player& p = s.p[who];
    ++s.turn;
    if (trade_window) trade_window(*this,who);
    else trade(who);
    for (int roll = 1; s.winner == -1; ++roll) {
        manage(who);
        manage(1 - who);
        bool trying_doubles = p.jail >= 0;
        if (trying_doubles) {
            int choice = ask_player(who, {Kind::Jail}).value;
            if ((choice == 2 || choice == 3) && (p.cards & (1u << (choice - 2)))) {
                return_card(who, choice - 2);
                trying_doubles = false;
            } else if (choice == 1 && p.cash >= 50) {
                pay(who, -1, 50);
                trying_doubles = false;
            }
            if (!trying_doubles) p.jail = -1;
        }
        observe(TableEvent::BeforeRoll, who);
        int first = roll_die(*this);
        int second = roll_die(*this);
        observe(TableEvent::Dice, who, first, second);
        bool doubles = first == second;
        if (trying_doubles) {
            if (!doubles && p.jail < 2) {
                ++p.jail;
                break;
            }
            if (!doubles && !pay(who, -1, 50)) break;
            p.jail = -1;
        } else if (doubles && roll == 3) {
            send_to_jail(p);
            break;
        }
        int sum = first + second;
        advance(*this, who, (p.pos + sum) % Squares, sum);
        if (!doubles || trying_doubles || p.jail >= 0) break;
    }
    s.active = 1 - who;
}

int Game::play(int cap) {
    while (s.winner < 0 && s.turn < cap) turn();
    return s.winner;
}

} // namespace mono
