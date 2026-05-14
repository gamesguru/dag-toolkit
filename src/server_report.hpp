#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace dag {

/// Per-server analysis report — mirrors the Python ServerReport dataclass.
struct ServerReport {
    std::string server;
    int64_t events = 0;
    int64_t min_depth = 0;
    int64_t max_depth = 0;
    std::string root;

    // Final state-res membership outcomes
    int64_t res_joined = 0;
    int64_t res_left = 0;
    int64_t res_banned = 0;

    double bf = 0.0;  // branching factor (avg prev_events per event)

    int64_t missing = 0;
    int64_t extra = 0;
    std::vector<std::string> missing_users;
    std::vector<std::string> extra_users;

    double precision = 0.0;
    double recall = 0.0;
    double f1 = 0.0;
};

/// Chain analysis result entry.
struct ChainResult {
    std::string server;
    int64_t joined = 0;
    int64_t left = 0;
    int64_t ban = 0;
    int64_t chain_len = 0;
    std::vector<std::string> partners;
};

}  // namespace dag
