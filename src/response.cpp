#include "response.hpp"
#include <cmath>
#include <cstring>
#include <fstream>

namespace mono {
namespace {
float scale(int cash,int unit) {
    return std::clamp(float(cash)/unit,-5.f,5.f);
}
void deed_features(OfferFeatures& f,int at,const State& s,int seller,int sq) {
    if (sq<0) return;
    const auto& d=board[sq];
    const int g=d.group, buy=1-seller;
    const int n=group_size[g], held=s.count[seller][g], wanted=s.count[buy][g];
    f[at++]=1.f;
    f[at++]=d.price/400.f;
    f[at++]=d.mortgage/200.f;
    f[at++]=d.house/200.f;
    f[at++]=s.mort[sq];
    f[at++]=g==Rail;
    f[at++]=g==Utility;
    f[at++]=float(held)/n;
    f[at++]=float(wanted)/n;
    f[at++]=held==n;
    f[at++]=wanted+1==n;
    f[at++]=d.rents[3]/1500.f;
    f[at++]=s.p[buy].cash/float(std::max(200,3*n*d.house));
    f[at]=std::min(3.f,s.p[seller].cash/1000.f);
}
}
OfferFeatures offer_features(const State& s,int proposer,const Action& a) {
    const int r=1-proposer;
    OfferFeatures f{};
    f[0]=scale(a.cash,500);
    f[1]=scale(s.p[r].cash,1000);
    f[2]=scale(s.p[proposer].cash,1000);
    f[3]=scale(s.p[r].cash+a.cash,1000);
    f[4]=scale(s.p[proposer].cash-a.cash,1000);
    f[5]=std::min(s.turn,160)/160.f;
    f[6]=s.houses/32.f;
    f[7]=(a.give>=0 && a.take>=0 && board[a.give].group==board[a.take].group);
    deed_features(f,8,s,proposer,a.give);
    deed_features(f,22,s,r,a.take);
    return f;
}
bool load_response(const char* path,ResponseNet& dest) {
    std::ifstream f(path,std::ios::binary);
    char header[8];
    if (!f.read(header,8) || std::memcmp(header,"MRESP001",8)!=0) return false;
    ResponseNet net;
    auto read=[&](float* start,int n) {
        if (!f.read(reinterpret_cast<char*>(start),n*sizeof(float))) return false;
        return std::all_of(start,start+n,[](float v){return std::isfinite(v);});
    };
    if (!read(net.w.data(),net.w.size()) || !read(net.bias.data(),net.bias.size())
        || !read(net.out.data(),net.out.size()) || !read(&net.intercept,1)
        || f.peek()!=std::ifstream::traits_type::eof()) return false;
    dest=net;
    return true;
}
float response_likelihood(const Policy& p,const State& s,int proposer,const Action& a) {
    if (!p.response) return -1.f;
    const auto x=offer_features(s,proposer,a);
    const auto& net=*p.response;
    auto hidden=net.bias;
    for (int i=0;i<ResponseInputs;++i) for (int j=0;j<ResponseHidden;++j)
        hidden[j]+=x[i]*net.w[i*ResponseHidden+j];
    float z=net.intercept;
    for (int j=0;j<ResponseHidden;++j) z+=std::max(0.f,hidden[j])*net.out[j];
    return 1.f/(1.f+std::exp(-std::clamp(z,-30.f,30.f)));
}
}
