#include "four/game.hpp"
#include <cassert>

namespace {
using namespace mono;
using Table=four::Game;

struct Trader {
    int offer_calls=0, reply_calls=0;
    bool agrees=false;
    Action proposal{1,-1,1,19,55};
};

Action choose(void* context,const State& pair,int who,const Query& q) {
    auto& trader=*static_cast<Trader*>(context);
    assert(who==0);
    if(q.kind==Kind::Offer) {
        ++trader.offer_calls;
        return trader.proposal;
    }
    if(q.kind==Kind::Accept) {
        ++trader.reply_calls;
        assert(q.give==1 && q.take==19 && q.cash==55);
        assert(pair.owner[1]==1 && pair.owner[19]==0);
        assert(pair.behavior[1].size>0);
        const auto& latest=pair.behavior[1].events[pair.behavior[1].size-1];
        assert(latest.kind==ProposedTrade && latest.turn==pair.turn);
        return {trader.agrees};
    }
    return {};
}

void test_projection() {
    Table g(91,{});
    g.active=2;
    g.owner[1]=0;
    g.owner[19]=1;
    g.owner[6]=2;
    g.owner[5]=3;
    g.level[6]=3;
    g.mort[5]=g.mort[1]=true;
    g.houses=29;
    g.hotels=12;
    g.personal_turns={5,4,4,4};
    g.players[0].cards=1;
    g.players[1].cards=2;
    g.observe(1,{BoughtAtFace,19,200,20});
    g.observe(1,{DeclinedTrade,1,45,20},0);
    g.rejected[1][0]={1,19,45,8};
    const auto s=g.view(0,1);
    assert(s.turn==9 && s.active==1 && s.houses==29 && s.hotels==12);
    assert(s.owner[1]==0 && s.owner[19]==1 && s.owner[6]==-1 && s.owner[5]==-1);
    assert(s.mort[1] && !s.mort[5] && s.level[6]==0 && s.mort_count[0]==1);
    assert(s.p[0].cards==1 && s.p[1].cards==2 && s.count[0][board[1].group]==1);
    assert(s.behavior[1].size==2 && s.behavior[1].events[0].turn==9 && s.rejected[1].give==1);
    auto third=g.view(2,1);
    assert(third.behavior[1].size==1 && third.behavior[1].events[0].turn==8 && third.rejected[1].give==-1);
    auto solo=g.view(0,-1);
    assert(solo.owner[1]==0 && solo.owner[19]==-1 && solo.owner[0]==-1);
    // Choose the rival who could complete the queried group before comparing equity.
    g.players[3].cash=200000;
    assert(g.focus(0,{Kind::Bid,18})==1 && g.focus(0,{Kind::Jail})==3);
    g.alive[3]=false;
    assert(g.focus(0,{Kind::Jail})==1);
    Table tied(1,{});
    assert(tied.focus(2,{Kind::Manage})==3);
}

void test_negotiation() {
    std::array<Trader,four::Seats> traders{};
    std::array<Policy,four::Seats> policies{};
    for(int p=0;p<four::Seats;++p) {
        policies[p].external=choose;
        policies[p].context=&traders[p];
    }
    Table g(4,policies);
    g.personal_turns={7,6,6,6};
    for(auto& p:g.players) p.cash=100;
    g.owner[1]=0;
    g.mort[1]=true;
    g.owner[19]=1;
    g.owner[3]=2;
    // Although the proposer tries the same swap in all three views, only its
    // true owner can be asked to sell 19.
    g.trade(0);
    assert(traders[0].offer_calls==3 && traders[1].reply_calls==1);
    assert(traders[2].reply_calls==0 && traders[3].reply_calls==0);
    assert(g.offers[0][1]==1 && g.accepted[0][1]==0 && g.owner[1]==0);
    for(int n:g.accepted_types[0]) assert(n==0);
    assert(g.rejected[0][1].take==19 && g.rejected[0][1].turn==13 && g.rejected[0][2].give==-1);
    assert(g.history[0][1].size==1 && g.history[1][0].events[0].kind==DeclinedTrade);
    assert(g.history[0][2].size==0 && g.history[1][2].size==0);
    traders[1].agrees=true;
    g.trade(0);
    assert(g.offers[0][1]==2 && g.accepted[0][1]==1 && g.rejected[0][1].give==-1);
    assert(g.history[1][0].events[1].kind==AcceptedTrade);
    assert(g.owner[1]==1 && g.owner[19]==0 && g.owner[3]==2 && g.mort[1]);
    assert(g.players[0].cash==45 && g.players[1].cash==152 && g.players[2].cash==100);
    g.trading=false;
    g.trade(0);
    assert(traders[0].offer_calls==6);
}

// Offer the same railroad for different amounts to distinguish ranking from a
// first-legal-partner implementation. The cheapest rival is also willing to
// accept, but should never learn it was considered.
Action sell_railroad(void* context,const State& pair,int,const Query& q) {
    if(q.kind==Kind::Offer) return {1,-1,5,-1,-pair.p[1].cash/2};
    if(q.kind==Kind::Accept) {
        ++*static_cast<int*>(context);
        return {1};
    }
    return {};
}

void test_partner_ranking() {
    std::array<int,four::Seats> replies{};
    std::array<Policy,four::Seats> pol{};
    for(int p=0;p<four::Seats;++p) {
        pol[p].external=sell_railroad;
        pol[p].context=&replies[p];
    }
    Table g(5,pol);
    g.owner[5]=0;
    g.players[1].cash=50;
    g.players[2].cash=100;
    g.players[3].cash=1000;
    g.trade(0);
    assert(g.owner[5]==3 && g.players[3].cash==500 && g.players[0].cash==2000);
    assert(replies[3]==1 && replies[1]==0 && replies[2]==0);
    assert(g.offers[0][3]==1 && g.accepted[0][3]==1);
}

Action agree_to_terms(void* context,const State&,int,const Query& q) {
    if(q.kind==Kind::Offer) return *static_cast<Action*>(context);
    return {q.kind==Kind::Accept};
}

void test_trade_types() {
    struct Example { int give,take,cash,category; };
    const Example examples[]={
        {-1,19,55,0}, {1,-1,-70,1},
        {1,19,0,2}, {1,19,55,2}, {1,19,-55,2},
        {1,-1,0,3}, {-1,19,0,3}, {1,-1,55,3}, {-1,19,-55,3}
    };
    for(auto ex:examples) {
        Action offer{1,-1,ex.give,ex.take,ex.cash};
        std::array<Policy,four::Seats> pol{};
        for(auto& p:pol) { p.external=agree_to_terms; p.context=&offer; }
        Table g(2,pol);
        g.alive={true,false,false,true};
        g.active=3;
        g.owner[1]=3;
        g.owner[19]=0;
        g.trade(3);
        assert(g.accepted[3][0]==1);
        assert(g.players[3].cash==1500-ex.cash && g.players[0].cash==1500+ex.cash);
        for(int seat=0;seat<four::Seats;++seat)
            for(int category=0;category<four::TradeTypes;++category)
                assert(g.accepted_types[seat][category]==(seat==3 && category==ex.category));
    }
}
}

void test_four_policy() {
    test_projection();
    test_negotiation();
    test_partner_ranking();
    test_trade_types();
}
