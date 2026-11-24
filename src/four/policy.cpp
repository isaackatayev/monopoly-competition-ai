#include "game.hpp"
#include "neural.hpp"
#include "../neural_trade.hpp"
#include <limits>

namespace mono::four {

State Game::view(int who,int other) const {
    State s;
    s.owner.fill(-1);
    s.p[0]=players[who];
    const bool has_other=other>=0 && other<Seats && other!=who;
    s.p[1].cash=0;
    s.active=active==who?0:1;
    s.houses=houses;
    s.hotels=hotels;
    // These policies interpret time in individual duel turns. Counting only
    // turns taken by this pair keeps their normal offer cadences after a knockout.
    s.turn=personal_turns[who];
    if(has_other) {
        s.p[1]=players[other];
        s.turn+=personal_turns[other];
        s.behavior={history[who][other],history[other][who]};
        s.rejected={rejected[who][other],rejected[other][who]};
    }
    for(int sq=0;sq<Squares;++sq) {
        const int holder=owner[sq];
        if(holder!=who && !(has_other && holder==other)) continue;
        const int side=holder==who?0:1;
        s.owner[sq]=static_cast<int8_t>(side);
        s.level[sq]=static_cast<int8_t>(level[sq]);
        s.mort[sq]=mort[sq];
        ++s.count[side][board[sq].group];
        s.mort_count[side]+=mort[sq];
    }
    return s;
}

int Game::focus(int who,const Query& q) const {
    const int group=q.square>=0 && q.square<Squares?board[q.square].group:-1;
    std::array<int,Seats> group_deeds{};
    std::array<int,Seats> wealth{};
    for(int p=0;p<Seats;++p) wealth[p]=players[p].cash;
    for(int sq=0;sq<Squares;++sq) {
        const int p=owner[sq];
        if(p<0 || p==who) continue;
        const Space& d=board[sq];
        group_deeds[p]+=d.group==group;
        wealth[p]+=d.price-(mort[sq]?d.mortgage:0)+level[sq]*(d.house/2);
    }
    int best=-1;
    for(int step=1;step<Seats;++step) {
        const int p=(who+step)%Seats;
        if(!alive[p]) continue;
        if(best<0 || group_deeds[p]>group_deeds[best] ||
                (group_deeds[p]==group_deeds[best] && wealth[p]>wealth[best])) best=p;
    }
    return best;
}

Action Game::ask(int who,const Query& q,int other) const {
    if(who<0 || who>=Seats || !alive[who]) return {};
    if(other<0) other=focus(who,q);
    if(other<0 || other>=Seats || other==who || !alive[other]) return {};
    const auto pair=view(who,other);
    const auto base=mono::ask(policies[who],pair,0,q);
    return multiplayer_weights[who]?multiplayer_action(*this,who,other,pair,q,base):base;
}

void Game::observe(int who,const BehaviorEvent& event,int partner) {
    for(int other=0;other<Seats;++other) {
        if(other==who || !alive[other] || (partner>=0 && partner!=other)) continue;
        auto stamped=event;
        stamped.turn=personal_turns[who]+personal_turns[other];
        history[who][other].append(stamped);
    }
}

namespace {
TradeType type_of(const Action& deal) {
    if(deal.give>=0 && deal.take>=0) return SwapDeeds;
    if(deal.take>=0 && deal.cash>0) return BuyDeed;
    if(deal.give>=0 && deal.cash<0) return SellDeed;
    return OtherTrade;
}

double offer_score(const Policy& actor,const State& pair,const Action& deal) {
    if(actor.net) return assess_trade(actor,pair,0,deal).expected_gain;
    return trade_deed_value(actor,pair,0,deal.give,false)
         + trade_deed_value(actor,pair,0,deal.take,true) - deal.cash;
}
}

void Game::trade(int who) {
    if(!trading || !alive[who]) return;
    int partner=-1;
    Action offer;
    double best=-std::numeric_limits<double>::infinity();
    for(int offset=1;offset<Seats;++offset) {
        const int rival=(who+offset)%Seats;
        if(!alive[rival]) continue;
        const State pair=view(who,rival);
        Action possible=mono::ask(policies[who],pair,0,{Kind::Offer});
        // Each projected seat has its exact holdings, buildings, liens and
        // cash. Deeds held by uninvolved seats are ineligible on either side.
        double score;
        if(multiplayer_trades_enabled(multiplayer_weights[who])) {
            const auto choice=multiplayer_offer(*this,who,rival,pair,possible);
            if(!choice.offer.value) continue;
            possible=choice.offer;
            score=choice.score;
        } else {
            if(!legal_trade(pair,0,possible)) continue;
            score=offer_score(policies[who],pair,possible);
        }
        if(score>best) {
            best=score;
            partner=rival;
            offer=possible;
        }
    }
    if(partner<0) return;
    ++offers[who][partner];
    const auto pair=view(who,partner);
    BehaviorEvent event{ProposedTrade,offer.give>=0?offer.give:offer.take,offer.cash,pair.turn,
                        offer.give,offer.take,offer_generosity(pair,0,offer)};
    observe(who,event,partner);
    const Query terms{Kind::Accept,-1,0,offer.give,offer.take,offer.cash};
    const bool agreed=ask(partner,terms,who).value==1;
    event.kind=agreed?AcceptedTrade:DeclinedTrade;
    observe(partner,event,who);
    if(!agreed) {
        rejected[who][partner]={offer.give,offer.take,offer.cash,pair.turn};
        return;
    }
    ++accepted[who][partner];
    ++accepted_types[who][type_of(offer)];
    rejected[who][partner]={};
    rejected[partner][who]={};
    players[who].cash-=offer.cash;
    players[partner].cash+=offer.cash;
    if(offer.give>=0) transfer(offer.give,partner);
    if(offer.take>=0) transfer(offer.take,who);
    if(offer.take>=0 && mort[offer.take]) mortgage_election(who,offer.take);
    if(offer.give>=0 && mort[offer.give]) mortgage_election(partner,offer.give);
}

} // namespace mono::four
