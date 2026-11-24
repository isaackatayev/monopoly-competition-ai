#include "four/neural.hpp"
#include <cassert>

namespace {
using namespace mono;
using Table=four::Game;
using Weights=std::array<double,four::MultiplayerParams>;

Action fixed(void*,const State&,int,const Query& q) {
    if (q.kind==Kind::Bid) return {150};
    if (q.kind==Kind::Buy || q.kind==Kind::Accept) return {1};
    return {};
}

void test_zero_policy_preserves_game() {
    std::array<Policy,four::Seats> bots={preset(0),preset(1),preset(2),preset(3)};
    Table plain(95,bots), residual(95,bots);
    Weights zero{};
    residual.multiplayer_weights.fill(zero.data());
    assert(!four::multiplayer_enabled(zero.data()));
    for (int i=0;i<120 && plain.living()>1;++i) {
        plain.step();
        residual.step();
        assert(plain.owner==residual.owner && plain.level==residual.level && plain.mort==residual.mort);
        assert(plain.offers==residual.offers && plain.accepted==residual.accepted);
        assert(plain.rng.s==residual.rng.s && plain.alive==residual.alive);
        for (int p=0;p<four::Seats;++p) {
            assert(plain.players[p].cash==residual.players[p].cash);
            assert(plain.players[p].pos==residual.players[p].pos);
        }
    }
}

void test_public_third_seat_affects_bid() {
    Table g(1,{});
    for (auto& p:g.policies) p.external=fixed;
    Weights w{};
    w[2]=2.;
    g.multiplayer_weights[0]=w.data();
    const Query bid{Kind::Bid,19,10};
    const int initial=g.ask(0,bid,1).value;
    // Seat 1's projected state is unchanged by placing the other two oranges
    // in seat 2's hands, but the network can see the auction would now enable a set.
    g.transfer(16,2);
    g.transfer(18,2);
    assert(g.ask(0,bid,1).value>initial+60);
    assert(g.ask(1,bid,0).value==150);
    g.players[0].cash=21;
    assert(g.ask(0,bid,1).value<=21);
}

void test_trade_orientation_and_standing() {
    Table g(3,{});
    for (auto& p:g.policies) p.external=fixed;
    g.transfer(16,0);
    g.transfer(18,0);
    g.transfer(19,1);
    g.personal_turns={5,4,4,4}; // The active pair may make a completion offer.
    Weights w{};
    w[9]=3.;
    w[10]=-4.;
    g.multiplayer_weights[0]=w.data();
    const auto choice=four::multiplayer_offer(g,0,1,g.view(0,1),{});
    assert(choice.offer.value==1 && choice.offer.take==19 && choice.offer.give==-1);
    assert(choice.offer.cash>0 && choice.offer.cash<g.players[0].cash);

    // An incoming offer to buy our last orange would hand a funded rival a
    // full group. Its quality should change with an uninvolved rival's wealth.
    g.transfer(19,0);
    g.transfer(16,1);
    g.transfer(18,1);
    g.players[2].cash=g.players[3].cash=100;
    w.fill(0.);
    w[14]=-4.;
    const Query request{Kind::Accept,-1,0,-1,19,200};
    assert(g.ask(0,request,1).value==0);
    g.players[2].cash=9000;
    assert(g.ask(0,request,1).value==1);

    // A three-way split means that consolidating our reds with seat 1
    // improves the bargaining position of seat 2, who then has the last red.
    w.fill(0.);
    w[13]=-4.;
    w[23]=-.35;
    g.transfer(24,0);
    g.transfer(23,1);
    const Query red{Kind::Accept,-1,0,-1,24,200};
    assert(g.ask(0,red,1).value==1);
    g.transfer(21,2);
    assert(g.ask(0,red,1).value==0);
}
}

void test_four_neural() {
    test_zero_policy_preserves_game();
    test_public_third_seat_affects_bid();
    test_trade_orientation_and_standing();
}
