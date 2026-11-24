#include "bundle_trade.hpp"
#include "groups.hpp"
#include "neural_value.hpp"
#include "neural_trade.hpp"
#include "opponent.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

namespace mono {
namespace {
using Mask = uint64_t;
constexpr Mask board_mask = (Mask{1} << Squares) - 1;
constexpr Mask asset_mask = (Mask{1} << (Squares+2)) - 1;
constexpr Mask card_mask = asset_mask & ~board_mask;
int card_count(unsigned cards) { return int(cards & 1u) + int((cards >> 1) & 1u); }
Mask bit(int square) { return Mask{1} << square; }

template<class F> void for_deed(Mask deeds, F visit) {
    while (deeds) {
        const int sq = __builtin_ctzll(deeds);
        visit(sq);
        deeds &= deeds - 1;
    }
}

Mask transferable(const State& s, int owner) {
    Mask result = Mask{s.p[owner].cards} << Squares;
    for (int g = 0; g < Groups; ++g) {
        const auto* squares = group_squares[g];
        if (std::any_of(squares, squares + group_size[g], [&](int sq) { return s.level[sq] > 0; }))
            continue;
        for (int i = 0; i < group_size[g]; ++i)
            if (s.owner[squares[i]] == owner) result |= bit(squares[i]);
    }
    return result;
}

// A cheap market estimate shortlists packages before scoring the complete new
// board with the trained network. The acceptance model trained on single-deed
// negotiations cannot represent the terms of a bundle.
double assembly(int g, int n) {
    if (g == Rail) return 13. * (1 << n);
    if (g == Utility) return n == 2 ? 45. : 0.;
    const int missing = group_size[g] - n;
    if (missing == 0) return .4 * board[group_squares[g][0]].rents[3] + 140.;
    return n > 0 && missing == 1 ? 55. : 0.;
}

struct Package {
    Mask assets = 0;
    int groups = 0, count = 0, equity = 0, fee = 0;
    double received = 0, surrendered = 0;
};

Package terms(const State& s, int sender, Mask deeds) {
    Package p;
    p.assets = deeds;
    const auto cards = static_cast<unsigned>(deeds >> Squares);
    p.count = card_count(cards);
    p.equity = 35 * p.count;
    int counts[Groups]{};
    for_deed(deeds & board_mask, [&](int sq) {
        const int g = board[sq].group;
        p.groups |= 1 << g;
        ++p.count;
        ++counts[g];
        p.equity += board[sq].price - (s.mort[sq] ? board[sq].mortgage : 0);
        if (s.mort[sq]) p.fee += (board[sq].mortgage + 9) / 10;
    });
    p.received = p.surrendered = p.equity;
    if (cards && s.p[1-sender].jail >= 0 && !s.p[1-sender].cards) p.received += 15;
    if (cards && s.p[sender].jail >= 0 && s.p[sender].cards == cards) p.surrendered += 15;
    for (int g = 0; g < Groups; ++g) {
        if (!counts[g]) continue;
        const int from = s.count[sender][g], to = s.count[1-sender][g];
        p.received += assembly(g, to + counts[g]) - assembly(g, to);
        p.surrendered += assembly(g, from) - assembly(g, from - counts[g]);
    }
    return p;
}

bool affordable(const State& s, int proposer, int cash, int own_fee, int other_fee) {
    const int64_t own = int64_t(s.p[proposer].cash) - cash - own_fee;
    const int64_t other = int64_t(s.p[1-proposer].cash) + cash - other_fee;
    return own >= 0 && other >= 0 && own <= std::numeric_limits<int>::max()
                                && other <= std::numeric_limits<int>::max();
}

void move_assets(State& s, Mask deeds, int from) {
    const auto cards = static_cast<unsigned>(deeds >> Squares);
    s.p[from].cards &= ~cards;
    s.p[1-from].cards |= cards;
    for_deed(deeds & board_mask, [&](int sq) {
        s.owner[sq] = static_cast<int8_t>(1-from);
        const int group = board[sq].group;
        --s.count[from][group];
        ++s.count[1-from][group];
        s.mort_count[from] -= s.mort[sq];
        s.mort_count[1-from] += s.mort[sq];
    });
}

bool legal_terms(const State& s,int p,Mask give,Mask take,int cash,Package& giving,Package& taking) {
    if ((p != 0 && p != 1) || (!(give | take) && cash != 0) || ((give | take) & ~asset_mask)
            || (give & ~transferable(s,p)) || (take & ~transferable(s,1-p))) return false;
    giving = terms(s,p,give);
    taking = terms(s,1-p,take);
    return affordable(s,p,cash,taking.fee,giving.fee);
}

void transfer_package(State& s,int p,const Package& give,const Package& take,int cash) {
    move_assets(s, give.assets, p);
    move_assets(s, take.assets, 1-p);
    s.p[p].cash -= cash + take.fee;
    s.p[1-p].cash += cash - give.fee;
}

// Losing the last spendable dollars is especially costly at the next rent payment.
double cash_change(int before,int after) {
    return after-before + .4*(std::min(500,after)-std::min(500,before))
                        + .55*(std::min(200,after)-std::min(200,before));
}

double willingness(double benefit) {
    return .5 + .48 * benefit / (std::abs(benefit) + 90.);
}

void describe(const State& after,BundleResult& result) {
    for (int seat = 0; seat < 2; ++seat) {
        result.cash_after[seat] = after.p[seat].cash;
        result.cards_after[seat] = static_cast<int>(after.p[seat].cards);
        for (int g = 0; g < Rail; ++g)
            if (after.count[seat][g] == group_size[g]) result.full_colors[seat] |= 1 << g;
    }
}

State score(const Policy& policy, const State& before, int p, const Package& give, const Package& take,
           int cash, float current, BundleResult& result) {
    result = {};
    result.give = give.assets;
    result.take = take.assets;
    result.cash = cash;
    result.fee[p] = take.fee;
    result.fee[1-p] = give.fee;

    // Project the legal election to retain all incoming mortgages. Both seats'
    // fees are paid at transfer. Future redemptions are separate host decisions.
    State after = before;
    transfer_package(after,p,give,take,cash);
    describe(after,result);
    double their_asset_change = give.equity - take.equity;
    if (before.p[1-p].jail >= 0)
        their_asset_change += 15. * (int(after.p[1-p].cards != 0)-int(before.p[1-p].cards != 0));
    for (int g = 0; g < Groups; ++g)
        their_asset_change += assembly(g, after.count[1-p][g]) - assembly(g, before.count[1-p][g]);
    result.before = current;
    result.after = neural_value(*policy.net, after, p);
    result.accept = result.before - result.after > .0005f;
    result.willingness = static_cast<float>(willingness(their_asset_change + cash_change(before.p[1-p].cash,after.p[1-p].cash)));
    result.gain = (result.after - result.before) * result.willingness;
    return after;
}

std::vector<Package> packages(const State& s, int sender) {
    const Mask legal = transferable(s,sender) & board_mask;
    std::vector<int> squares;
    for_deed(legal, [&](int sq) { squares.push_back(sq); });
    std::vector<Package> result(1), mixed[2];
    for (size_t i = 0; i < squares.size(); ++i) {
        const Mask single = bit(squares[i]);
        result.push_back(terms(s,sender,single));
        for (size_t j = i+1; j < squares.size(); ++j) {
            const Mask pair = single | bit(squares[j]);
            auto add = [&](Mask mask, int index) {
                auto package = terms(s,sender,mask);
                if ((package.groups & (package.groups-1)) == 0) result.push_back(package);
                else mixed[index].push_back(package);
            };
            add(pair,0);
            for (size_t k = j+1; k < squares.size(); ++k) add(pair | bit(squares[k]),1);
        }
    }
    Mask railroads = 0;
    for (int sq : group_squares[Rail]) railroads |= bit(sq);
    if ((legal & railroads) == railroads) result.push_back(terms(s,sender,railroads));
    for (auto& options : mixed) {
        // Retain both low-friction swaps and packages that complete a new set.
        std::stable_sort(options.begin(),options.end(),[](const Package& a,const Package& b) {
            return a.received-a.surrendered > b.received-b.surrendered;
        });
        result.insert(result.end(),options.begin(),options.begin()+std::min<size_t>(20,options.size()));
    }
    const size_t deeds_only = result.size();
    const unsigned held = s.p[sender].cards;
    for (unsigned cards = held; cards; cards = (cards-1) & held) {
        const Mask card_bits = Mask{cards} << Squares;
        for (size_t i = 0; i < deeds_only; ++i)
            result.push_back(terms(s,sender,result[i].assets | card_bits));
    }
    return result;
}

struct Candidate { const Package* give; const Package* take; int cash; double rank; };
constexpr size_t finalists = 24;
void shortlist(std::vector<Candidate>& best,Candidate entry) {
    if (best.size() == finalists && entry.rank <= best.back().rank) return;
    const auto pos = std::find_if(best.begin(),best.end(),[&](const Candidate& a) {return a.rank < entry.rank;});
    best.insert(pos,entry);
    if (best.size() > finalists) best.pop_back();
}
Policy rollout_policy(const Policy& policy) {
    Policy base = policy;
    base.style = 0;
    base.net = nullptr;
    base.deal_net = nullptr;
    base.external = nullptr;
    base.context = nullptr;
    return base;
}

bool skip_proposal(const Policy& policy,const State& s,int recipient) {
    float refusal = refusal_probability(policy,s,recipient);
    // Repeated actual package replies add evidence even for entrants that
    // have no learned opponent network. A clearly poor offer earns no veto evidence.
    const auto& history = s.behavior[recipient];
    for (int i = 0; i < history.size; ++i) {
        const auto& e = history.events[i];
        if (e.kind == AcceptedBundle) refusal *= .58f;
        if (e.kind == DeclinedBundle && e.generosity >= -100)
            refusal += (1.f-refusal) * (e.generosity >= 0 ? .22f : .10f);
    }
    return refusal > .82f && (s.turn/2)%8 != 0;
}

bool is_scalar(const Package& give,const Package& take) {
    return (give.assets || take.assets) && !((give.assets | take.assets) & card_mask)
           && give.count <= 1 && take.count <= 1;
}

void scalar_assessment(const Policy& policy,const State& s,int p,const Package& give,
                       const Package& take,int cash,BundleResult& result) {
    const Action offer{1,-1,give.assets ? __builtin_ctzll(give.assets) : -1,
                            take.assets ? __builtin_ctzll(take.assets) : -1,cash};
    const auto report = assess_trade(policy,s,p,offer);
    const Policy base = rollout_policy(policy);
    State after = s;
    settle_trade(after,p,offer,base,base);
    result = {};
    result.give = give.assets;
    result.take = take.assets;
    result.cash = cash;
    result.fee[p] = take.fee;
    result.fee[1-p] = give.fee;
    describe(after,result);
    result.scalar = 1;
    result.before = report.proposer_before;
    result.after = report.proposer_after;
    result.willingness = report.acceptance_estimate;
    result.gain = report.expected_gain;
    result.deal_gain = report.recipient_deal_gain;
    result.used_deal_model = report.recipient_uses_deal_model;
    result.accept = decide(policy,s,1-p,{Kind::Accept,-1,0,offer.give,offer.take,cash}).value == 1;
}

float continuation(const NeuralNet& net,const State& state,const Policy& base,int seat,uint64_t seed) {
    Game game(seed,base,base);
    game.s = state;
    game.s.turn = std::max(0,state.turn-1);
    game.trade_window = [](Game&,int) {};
    condition_decks_on_held_cards(game);
    game.play(game.s.turn+16);
    return neural_value(net,game.s,seat);
}

}

bool assess_bundle(const Policy& policy, const State& s, int p, Mask give, Mask take, int cash,
                   BundleResult& result) {
    Package giving, taking;
    if (!policy.net || !legal_terms(s,p,give,take,cash,giving,taking)) return false;
    if (is_scalar(giving,taking)) {
        scalar_assessment(policy,s,p,giving,taking,cash,result);
        return true;
    }
    const auto after = score(policy,s,p,giving,taking,cash,neural_value(*policy.net,s,p),result);
    const double immediate = result.before-result.after;
    if (!(give | take)) {
        result.willingness = 0;
    } else if (std::abs(immediate) < .12) {
        const Policy base = rollout_policy(policy);
        Rng seeds{uint64_t(s.turn)*0xa4597efa8924f555ULL+uint64_t(Squares*s.p[0].pos+s.p[1].pos)};
        double gain = 0;
        constexpr int pairs = 4;
        for (int i = 0; i < pairs; ++i) {
            const auto seed = seeds.next();
            gain += continuation(*policy.net,after,base,1-p,seed)-continuation(*policy.net,s,base,1-p,seed);
        }
        result.accept = .55*immediate+.45*gain/pairs > .0015;
    }
    return true;
}

bool settle_bundle_game(Game& game,int p,const BundleResult& deal) {
    const auto& before = game.s;
    Package giving, taking;
    if (!(deal.give | deal.take) || !legal_terms(before,p,deal.give,deal.take,deal.cash,giving,taking))
        return false;
    State s = before;
    transfer_package(s,p,giving,taking,deal.cash);
    s.rejected = {};
    // A held release card remains outside its deck until its new holder uses it.
    // All compulsory transfer fees have been paid. While asking about a deed's
    // immediate election, show its own reserved interest as part of the quoted
    // redemption price. The rest of the reserved interest stays unavailable.
    for (int seat : {p,1-p}) {
        Mask incoming = (seat == p ? deal.take : deal.give) & board_mask;
        for_deed(incoming,[&](int sq) {
            if (!s.mort[sq] || s.p[seat].cash < board[sq].mortgage) return;
            const int principal = board[sq].mortgage, fee = (principal+9)/10;
            State election = s;
            election.p[seat].cash += fee;
            const auto action = ask(*game.policies[seat],election,seat,{Kind::Manage,sq,principal+fee});
            if (action.value == Redeem && action.square == sq) {
                s.p[seat].cash -= principal;
                s.mort[sq] = false;
                --s.mort_count[seat];
            }
        });
    }
    game.s = s;
    return true;
}

bool propose_bundle(const Policy& policy, const State& s, int p, BundleResult& result) {
    if (!policy.net || (p != 0 && p != 1)) return false;
    result = {};
    if (skip_proposal(policy,s,1-p)) return true;
    float best_gain = .0005f;
    // Keep the competition policy's scalar proposal as the incumbent.
    const auto single = decide(policy,s,p,{Kind::Offer});
    if ((single.give >= 0 || single.take >= 0) &&
        assess_bundle(policy,s,p,single.give >= 0 ? bit(single.give) : 0,
                      single.take >= 0 ? bit(single.take) : 0,single.cash,result))
        best_gain = std::max(best_gain,result.gain);

    auto giving = packages(s,p), taking = packages(s,1-p);
    std::vector<Candidate> best;
    best.reserve(finalists+1);
    for (const auto& g : giving) for (const auto& t : taking) {
        if (g.count < 2 && t.count < 2 && !((g.assets | t.assets) & card_mask)) continue;
        if (g.groups & t.groups) continue; // Don't cycle same-group deeds in generated proposals.
        const int lo = g.fee-s.p[1-p].cash, hi = s.p[p].cash-t.fee;
        if (lo > hi) continue;
        const double mine = t.received - g.surrendered - t.fee;
        const double theirs = g.received - t.surrendered - g.fee;
        const double surplus = mine + theirs;
        const double face = t.equity - g.equity;
        const double prices[] = {0., face, .85*face, 1.15*face, -theirs+.25*surplus,
                                 -theirs+.5*surplus, -theirs+.75*surplus};
        int seen[7], size = 0;
        for (double price : prices) {
            const int cash = std::clamp(static_cast<int>(std::round(price/5.))*5,lo,hi);
            if (std::find(seen,seen+size,cash) != seen+size) continue;
            seen[size++] = cash;
            if ((!g.assets && cash <= 0) || (!t.assets && cash >= 0)) continue;
            const double own_benefit = t.received - g.surrendered
                    + cash_change(s.p[p].cash,s.p[p].cash-cash-t.fee);
            const double their_benefit = g.received - t.surrendered
                    + cash_change(s.p[1-p].cash,s.p[1-p].cash+cash-g.fee);
            if (own_benefit <= 0 || their_benefit < -90) continue;
            const double rank = own_benefit*willingness(their_benefit);
            shortlist(best,{&g,&t,cash,rank});
        }
    }
    if (!best.empty()) {
        const float current = neural_value(*policy.net,s,p);
        for (const auto& c : best) {
            BundleResult candidate;
            score(policy,s,p,*c.give,*c.take,c.cash,current,candidate);
            if (candidate.gain > best_gain) {
                result = candidate;
                best_gain = candidate.gain;
            }
        }
    }
    return true;
}
}
