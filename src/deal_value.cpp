#include "deal_value.hpp"
#include <cmath>
#include <fstream>
#include <cstring>
namespace mono {
DealFeatures deal_features(const Policy& p,const State& s,const State& after,int r,const Action& a) {
    const auto offer=offer_features(s,1-r,a);
    const auto before_x=neural_features(s,r), after_x=neural_features(after,r);
    DealFeatures x{};
    auto cursor=std::copy(offer.begin(),offer.end(),x.begin());
    for(int change=0;change<2;++change) for(int seat=0;seat<2;++seat)
        for(int i=50;i<62;++i) {
            const int index=4+62*seat+i;
            *cursor++=change?after_x[index]-before_x[index]:before_x[index];
        }
    x[84]=neural_value(*p.net,s,r);
    x[85]=neural_value(*p.net,after,r)-x[84];
    return x;
}
bool load_deal_net(const char* path,DealNet& net) {
    std::ifstream f(path,std::ios::binary); char header[8];
    if(!f.read(header,8)||std::memcmp(header,"MDEAL001",8)) return false;
    auto take=[&](float* data,size_t n){
        return f.read(reinterpret_cast<char*>(data),n*sizeof(float)) &&
            std::all_of(data,data+n,[](float v){return std::isfinite(v);});
    };
    return take(net.w.data(),net.w.size()) && take(net.bias.data(),net.bias.size()) &&
           take(net.out.data(),net.out.size()) && take(&net.intercept,1) &&
           f.peek()==std::ifstream::traits_type::eof();
}
float recipient_gain(const DealNet& net,const DealFeatures& x) {
    auto h=net.bias;
    for(int i=0;i<DealInputs;++i) for(int j=0;j<DealHidden;++j) h[j]+=x[i]*net.w[i*DealHidden+j];
    float v=net.intercept+x[85];
    for(int j=0;j<DealHidden;++j) v+=std::max(0.f,h[j])*net.out[j];
    return std::clamp(v,-1.f,1.f);
}
}
