#include "neural_value.hpp"
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <vector>

namespace mono {
namespace {
// Board visitation weights (per thousand resolved landings) include card movement and jail.
constexpr int visits[Squares] = {
    31,21,19,22,23,30,23,8,23,23,59,27,26,24,25,29,28,26,29,31,
    29,28,11,27,32,31,27,27,28,26,0,27,27,24,25,25,9,22,22,26
};

float score(const NeuralNet& n, const NeuralFeatures& x) {
    auto h1 = n.b1;
    for (int i = 0; i < NeuralInputs; ++i) {
        const float v = x[i];
        if (!v) continue;
        const float* weights = n.w1.data() + i * Hidden1;
        for (int k = 0; k < Hidden1; ++k) h1[k] += v * weights[k];
    }
    auto h2 = n.b2;
    for (int i = 0; i < Hidden1; ++i) {
        if (h1[i] <= 0) continue;
        const float* weights = n.w2.data() + i * Hidden2;
        for (int k = 0; k < Hidden2; ++k) h2[k] += h1[i] * weights[k];
    }
    float value = 0;
    for (int i = 0; i < Hidden2; ++i) value += std::max(0.f, h2[i]) * n.w3[i];
    return value;
}

uint32_t read_u32(const unsigned char* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
}

NeuralFeatures neural_features(const State& s, int who) {
    NeuralFeatures f{};
    f[0] = std::min(3.f, s.turn / 100.f);
    f[1] = s.houses / 32.f;
    f[2] = s.hotels / 12.f;
    f[3] = std::min(1.f, s.turn / 24.f);
    int liquidity[2]{}, income[2]{}, upcoming[2]{}, max_next[2]{}, max_any[2]{}, sets[2]{};
    int mort_count[2][Groups]{}, levels[2][Groups]{}, rent_sum[2][Groups]{};
    for (int sq = 0; sq < Squares; ++sq) {
        const int owner = s.owner[sq];
        if (owner < 0) continue;
        const auto& deed = board[sq];
        const int group = deed.group, lv = s.level[sq];
        const int r = rent(s, sq, 7);
        mort_count[owner][group] += s.mort[sq];
        levels[owner][group] += lv;
        rent_sum[owner][group] += r;
        liquidity[owner] += (s.mort[sq] ? 0 : deed.mortgage) + lv * deed.house / 2;
        income[owner] += visits[sq] * r;
        int payer = 1 - owner;
        max_any[payer] = std::max(max_any[payer], r);
        int gap = (sq - s.p[payer].pos + Squares) % Squares;
        if (gap >= 2 && gap <= 12) {
            upcoming[payer] += (6 - std::abs(gap - 7)) * r;
            max_next[payer] = std::max(max_next[payer], r);
        }
    }
    for (int rel = 0; rel < 2; ++rel) {
        const int p = rel == 0 ? who : 1 - who;
        int j = GlobalFeatures + rel * SeatFeatures;
        for (int g = 0; g < Groups; ++g) {
            const float n = float(group_size[g]);
            const bool complete = s.count[p][g] == group_size[g];
            f[j++] = s.count[p][g] / n;
            f[j++] = float(complete);
            f[j++] = mort_count[p][g] / n;
            f[j++] = levels[p][g] / (5.f*n);
            f[j++] = rent_sum[p][g] / (g < Rail ? 1500.f : 400.f);
            sets[p] += g < Rail && complete && !mort_count[p][g];
        }
        const auto& player = s.p[p];
        f[j++] = std::min(4.f, player.cash / 1500.f);
        f[j++] = std::min(5.f, (player.cash + liquidity[p]) / 1000.f);
        f[j++] = player.cash / (player.cash + 600.f);
        f[j++] = player.pos / 39.f;
        f[j++] = (player.jail + 1) / 3.f;
        f[j++] = ((player.cards & 1u) + ((player.cards >> 1) & 1u)) / 2.f;
        f[j++] = upcoming[p] / (36.f * 450.f);
        f[j++] = max_next[p] / 1500.f;
        f[j++] = max_any[p] / 2000.f;
        f[j++] = income[p] / 250000.f;
        f[j++] = sets[p] / 3.f;
        f[j++] = float(s.active == p);
    }
    return f;
}

float neural_value(const NeuralNet& net, const State& s, int who) {
    if (s.winner >= 0) return float(s.winner == who);
    auto features = neural_features(s, who);
    float mine = score(net, features);
    auto a = features.begin() + GlobalFeatures;
    std::swap_ranges(a, a + SeatFeatures, a + SeatFeatures);
    float logit = (mine - score(net, features)) / net.temperature;
    return 1.f / (1.f + std::exp(-std::clamp(logit, -32.f, 32.f)));
}

bool load_neural(const char* path, NeuralNet& destination) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559, "IEEE float32 required");
    constexpr int floats = NeuralInputs*Hidden1 + Hidden1 + Hidden1*Hidden2 + 2*Hidden2 + 1;
    constexpr int header = 20, file_size = header + 4*floats + 8;
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file || file.tellg() != file_size) return false;
    std::vector<unsigned char> bytes(file_size);
    file.seekg(0);
    if (!file.read(reinterpret_cast<char*>(bytes.data()), file_size)) return false;
    const unsigned char* data = bytes.data();
    if (std::memcmp(data, "MNVNET01", 8) || read_u32(data+8) != NeuralInputs ||
        read_u32(data+12) != Hidden1 || read_u32(data+16) != Hidden2) return false;
    uint64_t hash = 14695981039346656037ULL;
    for (int i = 0; i < file_size - 8; ++i) hash = (hash ^ data[i]) * 1099511628211ULL;
    const unsigned char* trailer = data + file_size - 8;
    if (hash != (uint64_t(read_u32(trailer+4)) << 32 | read_u32(trailer))) return false;
    NeuralNet net;
    int offset = header;
    auto next = [&](float& v) {
        uint32_t raw = read_u32(data + offset);
        offset += 4;
        std::memcpy(&v, &raw, 4);
        return std::isfinite(v) && std::abs(v) < 10000;
    };
    auto copy = [&](auto& layer) {
        for (float& v : layer) if (!next(v)) return false;
        return true;
    };
    if (!copy(net.w1) || !copy(net.b1) || !copy(net.w2) || !copy(net.b2) ||
        !copy(net.w3) || !next(net.temperature) || net.temperature <= 0) return false;
    destination = net;
    return true;
}
} // namespace mono
