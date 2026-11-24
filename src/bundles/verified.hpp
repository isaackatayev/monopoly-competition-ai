#pragma once
#include "../bundle_trade.hpp"

namespace mono {
// Call these only for entrants that opted into v4 package verification.
bool verified_bundle_offer(const Policy&,const State&,int proposer,BundleResult&);
bool verified_bundle_assessment(const Policy&,const State&,int proposer,uint64_t give,uint64_t take,
                                int cash,BundleResult&);
inline bool package_offer(const Policy& p,const State& s,int proposer,BundleResult& result) {
    return p.style==10 || p.style==11 ? verified_bundle_offer(p,s,proposer,result)
                                      : propose_bundle(p,s,proposer,result);
}
inline bool package_assessment(const Policy& p,const State& s,int proposer,uint64_t give,uint64_t take,
                               int cash,BundleResult& result) {
    return p.style==10 || p.style==11 ? verified_bundle_assessment(p,s,proposer,give,take,cash,result)
                                      : assess_bundle(p,s,proposer,give,take,cash,result);
}
}
