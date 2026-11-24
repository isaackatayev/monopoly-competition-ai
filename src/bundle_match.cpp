#include "bundle_match.hpp"
#include "bundle_trade.hpp"
#include "bundles/verified.hpp"
#include <stdexcept>

namespace mono {
namespace {
constexpr uint64_t deed_mask=(uint64_t{1}<<Squares)-1;
bool multiple(uint64_t mask) { return mask && (mask & (mask-1)); }
int first(uint64_t mask) {
    for(int i=0;i<Squares;++i) if(mask&(uint64_t{1}<<i)) return i;
    return -1;
}
uint64_t mask(int sq) {return sq<0?0:(uint64_t{1}<<sq);}
void negotiate(Game& game,int p) {
    auto& stats=*static_cast<BundleStats*>(game.trade_window_context);
    const auto& sender=*game.policies[p];
    const auto& receiver=*game.policies[1-p];
    const auto& board_model=sender.net ? sender : receiver;
    auto& s=game.s;
    BundleResult offer;
    if(sender.net) {
        if(!package_offer(sender,s,p,offer)) return;
    } else {
        auto move=decide(sender,s,p,{Kind::Offer});
        if(!legal_trade(s,p,move)) return;
        offer.give=mask(move.give);offer.take=mask(move.take);offer.cash=move.cash;
    }
    if(!(offer.give|offer.take)) return;
    const bool cards=((offer.give|offer.take)&~deed_mask)!=0;
    const bool several=multiple(offer.give&deed_mask)||multiple(offer.take&deed_mask);
    const bool package=cards||several;
    const Action terms{1,-1,first(offer.give),first(offer.take),offer.cash};
    const int square=terms.give<0?terms.take:terms.give;
    BehaviorEvent event = package
        ? BehaviorEvent{ProposedBundle,-1,terms.cash,s.turn,-1,-1,0}
        : BehaviorEvent{ProposedTrade,square,terms.cash,s.turn,terms.give,terms.take,offer_generosity(s,p,terms)};
    s.behavior[p].append(event);
    BundleResult review;
    if(!(receiver.net ? package_assessment(receiver,s,p,offer.give,offer.take,offer.cash,review)
                      : assess_bundle(board_model,s,p,offer.give,offer.take,offer.cash,review)))
        throw std::logic_error("Sender generated an illegal trade");
    ++stats.offers; ++game.offers_made[p]; stats.package_offers+=package;
    // Scripted package counterparties use an asset-value reservation threshold.
    // Their original single-deed responses and the blocker's unconditional veto still apply.
    bool accept=review.accept;
    if(!package) accept=decide(receiver,s,1-p,{Kind::Accept,-1,0,terms.give,terms.take,terms.cash}).value==1;
    else if(!receiver.net) accept=receiver.style!=4 && review.willingness>=.55f;
    event.kind=package ? (accept?AcceptedBundle:DeclinedBundle) : (accept?AcceptedTrade:DeclinedTrade);
    s.behavior[1-p].append(event);
    if(!accept) {
        if(!package)s.rejected[p]={terms.give,terms.take,terms.cash,s.turn};
        return;
    }
    if(!settle_bundle_game(game,p,offer)) throw std::logic_error("Legal trade failed settlement");
    ++game.offers_closed[p]; ++stats.accepted; stats.package_accepted+=package;
    stats.multi_deed_accepted+=several; stats.card_accepted+=cards;
}
}
void bundle_matches(const Policy& a,const Policy& b,uint64_t seed,int games,int cap,BundleStats& stats) {
    Rng seeds{seed}; uint64_t board_seed=0;
    for(int n=0;n<games;++n) {
        const int seat=n%2;
        if(!seat)board_seed=seeds.next();
        Game g(board_seed,seat?b:a,seat?a:b);
        g.trade_window=negotiate;g.trade_window_context=&stats;
        const int winner=g.play(cap);stats.turns+=g.s.turn;
        stats.wins+=winner==seat;stats.losses+=winner==1-seat;stats.caps+=winner<0;
    }
}
}
