#pragma once
#include "game.hpp"

namespace mono {
struct BundleStats {
    int64_t wins=0,losses=0,caps=0,turns=0;
    int64_t offers=0,accepted=0,package_offers=0,package_accepted=0;
    int64_t multi_deed_accepted=0,card_accepted=0;
};
void bundle_matches(const Policy&,const Policy&,uint64_t seed,int games,int cap,BundleStats&);
}
