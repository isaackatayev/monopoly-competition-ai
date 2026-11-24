#include "groups.hpp"

#include <cassert>
#include <climits>

using namespace mono;
namespace {
struct Script {
    int buy=0, bid=0, accept=0, manage_steps=0;
    Action managing{}, offer{}, liquidation{};
    bool redeem_transfer=false;
};
Action scripted(void* data, const State&, int, const Query& q) {
    auto& p = *static_cast<Script*>(data);
    switch(q.kind) {
    case Kind::Buy: return {p.buy};
    case Kind::Bid: return {p.bid};
    case Kind::Accept: return {p.accept};
    case Kind::Offer: return p.offer;
    case Kind::Liquidate: return p.liquidation;
    case Kind::Manage:
        if (q.square >= 0) return p.redeem_transfer ? Action{Redeem,q.square} : Action{};
        if (p.manage_steps > 0) {
            --p.manage_steps;
            return p.managing;
        }
        return {};
    default: return {};
    }
}
struct Fixture {
    Script scripts[2];
    Policy left{}, right{};
    Game game;
    State& s;
    Fixture() : game(42, left, right), s(game.s) {
        left.external=right.external=scripted;
        left.context=&scripts[0]; right.context=&scripts[1];
    }
    void own(int who, int square) {
        assert(s.owner[square]==-1);
        s.owner[square]=who;
        ++s.count[who][board[square].group];
    }
    void group(int who, int group) {
        for(int i=0;i<group_size[group];++i) own(who,group_squares[group][i]);
    }
    void manage(int who, Act action, int square) {
        scripts[who].managing={action,square};
        scripts[who].manage_steps=1;
        game.manage(who);
    }
};
}

void test_economy() {
    {
        Fixture f;
        f.scripts[1].buy=1;
        f.game.property(1,3,7,0);
        assert(f.s.p[1].cash==1440 && f.s.owner[3]==1);
        f.scripts[0].bid=170;
        f.scripts[1].bid=200;
        f.game.property(0,1,7,0);
        assert(f.s.p[1].cash==1269 && f.s.owner[1]==1);
        assert(f.s.count[1][0]==2 && rent(f.s,3,7)==8);
        f.scripts[0].bid=3;
        f.scripts[1].bid=9;
        f.game.property(0,5,7,0);
        assert(f.s.owner[5]==-1);
        f.scripts[0].bid=500;
        f.s.p[0].cash=35;
        f.game.property(0,5,7,0);
        assert(f.s.owner[5]==0 && f.s.p[0].cash==25);
        f.game.property(1,5,7,1);
        assert(f.s.p[0].cash==75);
    }
    {
        Fixture f;
        f.group(0,Rail); f.group(0,Utility); f.group(0,0);
        f.manage(0,Mortgage,1);
        assert(rent(f.s,1,3)==0 && rent(f.s,3,3)==8 && !can_build(f.s,0,3));
        f.manage(0,Redeem,1);
        assert(!f.s.mort[1] && f.s.p[0].cash==1497 && f.s.mort_count[0]==0);
        f.manage(0,Mortgage,5);
        assert(rent(f.s,5,9)==0 && rent(f.s,15,9,1)==400);
        assert(rent(f.s,12,5)==50);
        f.manage(0,Mortgage,28);
        assert(rent(f.s,12,5)==50);
        f.manage(0,Redeem,28);
        assert(f.s.p[0].cash==1589); // A75 mortgage requires A83 to redeem.
        assert(!can_build(f.s,0,-1) && !can_sell(f.s,0,Squares));
        assert(!can_redeem(f.s,2,28) && !can_mortgage(f.s,0,4));
    }
    {
        Fixture f;
        f.group(0,0);
        f.manage(0,Build,1);
        assert(!can_build(f.s,0,1) && can_build(f.s,0,3));
        assert(!can_mortgage(f.s,0,3));
        f.manage(0,Build,3);
        for(int i=1;i<=3;++i) {
            f.manage(0,Build,1); f.manage(0,Build,3);
        }
        assert(f.s.houses==24 && f.s.hotels==12);
        f.manage(0,Build,1);
        assert(f.s.level[1]==5 && f.s.houses==28 && f.s.hotels==11);
        assert(!can_sell(f.s,0,3) && !can_build(f.s,0,1));
        f.manage(0,Sell,1);
        assert(f.s.houses==24 && f.s.hotels==12 && f.s.level[1]==4);
        f.manage(0,Build,1);
        f.s.houses=0; // All remaining houses occupied elsewhere.
        const int before=f.s.p[0].cash;
        f.manage(0,Sell,1);
        assert(f.s.level[1]==0 && f.s.level[3]==0 && f.s.hotels==12 && f.s.houses==4);
        assert(f.s.p[0].cash==before+225);
    }
    {
        Fixture f;
        f.group(0,0); f.group(1,1);
        f.s.houses=1;
        f.scripts[1].manage_steps=1;
        f.scripts[1].managing={Build,6};
        f.scripts[0].bid=99;
        f.scripts[1].bid=130;
        f.manage(0,Build,1);
        assert(f.s.level[6]==1 && f.s.level[1]==0 && f.s.houses==0);
        assert(f.s.p[1].cash==1400);
    }
    {
        Fixture f;
        f.group(0,0);
        f.s.p[0].cash=0;
        f.scripts[0].liquidation={Build,INT_MAX}; // invalid response
        assert(f.game.pay(0,1,45));
        assert(f.s.p[0].cash==15 && f.s.p[1].cash==1545 && f.s.mort_count[0]==2);
        assert(!f.game.pay(0,1,25));
        assert(f.s.winner==1 && f.s.p[0].cash==0 && f.s.p[1].cash==1560);
        assert(f.s.owner[1]==1 && f.s.count[1][0]==2 && f.s.mort_count[1]==2);
    }
    {
        Fixture f;
        f.group(0,0); f.own(1,12);
        f.scripts[1].accept=1;
        f.scripts[0].offer={0,-1,1,12,100};
        f.manage(0,Build,3);
        f.game.trade(0);
        assert(f.s.owner[1]==0 && f.s.owner[12]==1);
        f.manage(0,Sell,3);
        f.manage(1,Mortgage,12);
        f.s.turn=17;
        f.scripts[1].accept=0;
        f.game.trade(0);
        assert(f.s.rejected[0].give==1 && f.s.rejected[0].take==12 &&
               f.s.rejected[0].cash==100 && f.s.rejected[0].turn==17);
        f.scripts[1].accept=1;
        f.game.trade(0);
        assert(f.s.rejected[0].turn==0 && f.s.rejected[1].turn==0);
        assert(f.s.owner[1]==1 && f.s.owner[12]==0 && f.s.mort_count[0]==1);
        assert(f.s.p[0].cash==1367 && f.s.p[1].cash==1675);
        f.scripts[1].offer={0,-1,-1,12,25};
        f.scripts[0].accept=1;
        f.scripts[1].redeem_transfer=true;
        f.game.trade(1);
        assert(f.s.owner[12]==1 && !f.s.mort[12] && f.s.p[1].cash==1567);
        f.scripts[1].offer={0,-1,12,-1,INT_MIN};
        f.game.trade(1);
        assert(f.s.owner[12]==1 && f.s.p[1].cash==1567);
    }
}
