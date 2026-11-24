#include "game.hpp"
#include <numeric>
#include <stdexcept>

namespace mono::four {
namespace {

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

int die(Game& g) { return 1 + g.rng.below(6); }

void jail(Player& p) {
    p.pos = 10;
    p.jail = 0;
}

void advance(Game& g, int who, int square, int total = 0, int special = 0) {
    Player& p = g.players[who];
    if (square < p.pos) p.cash += 200;
    p.pos = square;
    g.land(who, total, special);
}

void put_back(Deck& deck, int id) {
    deck.ids[(deck.head + deck.size) % 16] = id;
    ++deck.size;
}

int repair_bill(const Game& g, int seat, int per_house, int per_hotel) {
    int bill = 0;
    for (int sq = 0; sq < Squares; ++sq) {
        if (g.owner[sq] == seat)
            bill += g.level[sq] == 5 ? per_hotel : g.level[sq] * per_house;
    }
    return bill;
}

// Resolve the card's public obligations in clockwise order. A payment can
// eliminate a participant, including a recipient that inherits mortgaged deeds.
void pay_each(Game& g, int drawer, int amount, bool collect) {
    for (int offset = 1; offset < Seats && g.alive[drawer] && g.living() > 1; ++offset) {
        const int other = (drawer + offset) % Seats;
        if (!g.alive[other]) continue;
        if (collect) g.pay(other, drawer, amount);
        else g.pay(drawer, other, amount);
    }
}

} // namespace

Game::Game(uint64_t seed, const std::array<Policy,Seats>& entrants, int seats)
        : initial_seats(seats), rng{seed}, policies(entrants) {
    if (seats != 3 && seats != Seats) throw std::invalid_argument("Expected three or four players");
    for (int seat = seats; seat < Seats; ++seat) {
        alive[seat] = false;
        players[seat].cash = 0;
    }
    owner.fill(-1);
    for (Deck& d : decks) {
        std::iota(d.ids.begin(), d.ids.end(), 0);
        for (int i = 15; i > 0; --i)
            std::swap(d.ids[i], d.ids[rng.below(i + 1)]);
    }
}

int Game::living() const {
    return static_cast<int>(std::count(alive.begin(), alive.end(), true));
}

int Game::winner() const {
    if (living() != 1) return -1;
    return static_cast<int>(std::find(alive.begin(), alive.end(), true) - alive.begin());
}

void Game::return_card(int seat, int index) {
    auto& p = players[seat];
    const unsigned bit = 1u << index;
    if (p.cards & bit) {
        p.cards &= ~bit;
        put_back(decks[index], release_card[index]);
    }
}

void Game::card(int who, int index) {
    Player& p = players[who];
    Deck& pile = decks[index];
    const int id = pile.ids[pile.head];
    pile.head = (pile.head + 1) % 16;
    --pile.size;
    if (id == release_card[index]) {
        p.cards |= 1u << index;
        return;
    }
    put_back(pile, id);

    if (index == 0) {
        switch (id) {
            case ToGo: advance(*this, who, 0); break;
            case ToIllinois: advance(*this, who, 24); break;
            case ToStCharles: advance(*this, who, 11); break;
            case ToUtility: advance(*this, who, (p.pos < 12 || p.pos >= 28) ? 12 : 28, 0, 2); break;
            case ToRailA: case ToRailB:
                advance(*this, who, ((p.pos + 5) / 10 * 10 + 5) % Squares, 0, 1);
                break;
            case Dividend: p.cash += 50; break;
            case BackThree:
                p.pos = (p.pos + Squares - 3) % Squares;
                land(who, 0);
                break;
            case ChanceJail: jail(p); break;
            case GeneralRepairs: pay(who, -1, repair_bill(*this, who, 25, 100)); break;
            case Speeding: pay(who, -1, 15); break;
            case ToReading: advance(*this, who, 5); break;
            case ToBoardwalk: advance(*this, who, 39); break;
            case Chairman: pay_each(*this, who, 50, false); break;
            case Loan: p.cash += 150; break;
        }
        return;
    }

    switch (id) {
        case ChestGo: advance(*this, who, 0); break;
        case BankError: p.cash += 200; break;
        case Doctor: pay(who, -1, 50); break;
        case Stock: p.cash += 50; break;
        case ChestJail: jail(p); break;
        case Holiday: p.cash += 100; break;
        case Refund: p.cash += 20; break;
        case Birthday: pay_each(*this, who, 10, true); break;
        case Insurance: p.cash += 100; break;
        case Hospital: pay(who, -1, 100); break;
        case School: pay(who, -1, 50); break;
        case Consultancy: p.cash += 25; break;
        case StreetRepairs: pay(who, -1, repair_bill(*this, who, 40, 115)); break;
        case Prize: p.cash += 10; break;
        case Inherit: p.cash += 100; break;
    }
}

void Game::land(int who, int total, int special) {
    const int square = players[who].pos;
    if (board[square].group >= 0) {
        if (board[square].group == Utility && owner[square] >= 0 &&
            owner[square] != who && !mort[square]) {
            const int first = die(*this);
            total = first + die(*this);
        }
        property(who, square, total, special);
        return;
    }
    switch (square) {
        case 2: case 17: case 33: card(who, 1); break;
        case 7: case 22: case 36: card(who, 0); break;
        case 4: pay(who, -1, 200); break;
        case 38: pay(who, -1, 100); break;
        case 30: jail(players[who]); break;
    }
}

void Game::step() {
    if (living() <= 1) return;
    while (!alive[active]) active = (active + 1) % Seats;
    const int who = active;
    auto& p = players[who];
    ++turn;
    ++personal_turns[who];
    if (trading) trade(who);
    for (int roll = 1; alive[who] && living() > 1; ++roll) {
        for (int offset = 0; offset < Seats; ++offset) {
            const int seat = (who + offset) % Seats;
            if (alive[seat]) manage(seat);
        }
        bool trying_doubles = p.jail >= 0;
        if (trying_doubles) {
            const int choice = ask(who, {Kind::Jail}).value;
            if ((choice == 2 || choice == 3) && (p.cards & (1u << (choice - 2)))) {
                return_card(who, choice - 2);
                trying_doubles = false;
            } else if (choice == 1 && p.cash >= 50) {
                pay(who, -1, 50);
                trying_doubles = false;
            }
            if (!trying_doubles) p.jail = -1;
        }
        const int first = die(*this);
        const int second = die(*this);
        const bool doubles = first == second;
        if (trying_doubles) {
            if (!doubles && p.jail < 2) {
                ++p.jail;
                break;
            }
            if (!doubles && !pay(who, -1, 50)) break;
            p.jail = -1;
        } else if (doubles && roll == 3) {
            jail(p);
            break;
        }
        const int total = first + second;
        advance(*this, who, (p.pos + total) % Squares, total);
        if (!doubles || trying_doubles || p.jail >= 0) break;
    }
    do {
        active = (active + 1) % Seats;
    } while (!alive[active]);
}

int Game::play(int cap) {
    while (living() > 1 && turn < cap) step();
    return winner();
}

} // namespace mono::four
