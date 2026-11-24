#pragma once
#include <initializer_list>

namespace mono {
// Optional, thread-local instrumentation for the local competition runner.
// The outer policy records last, replacing intermediate bidder estimates.
struct BidMetric { const char* name; double value; };
struct BidTrace { const char* reason; BidMetric metrics[12]; int count; };
inline thread_local BidTrace* current_bid_trace = nullptr;
inline void explain_bid(const char* reason, std::initializer_list<BidMetric> metrics) {
    if (!current_bid_trace) return;
    current_bid_trace->reason = reason;
    current_bid_trace->count = 0;
    for (auto metric : metrics) {
        if (current_bid_trace->count == 12) break;
        current_bid_trace->metrics[current_bid_trace->count++] = metric;
    }
}
}
