#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace dag {

/// Minimal Matrix PDU representation extracted from JSONL.
struct Event {
    std::string event_id;
    std::string room_id;
    std::string sender;
    std::string type;
    std::string state_key;  // empty for non-state events
    int64_t depth = 0;
    int64_t origin_server_ts = 0;
    std::vector<std::string> prev_events;
    std::vector<std::string> auth_events;
    std::string membership;  // content.membership (for m.room.member events)
};

/// Per-depth bucket for branching factor profiling.
struct DepthBucket {
    int64_t event_count = 0;
    int64_t total_prev_events = 0;

    [[nodiscard]] double bf() const {
        return event_count > 0
            ? static_cast<double>(total_prev_events) / static_cast<double>(event_count)
            : 0.0;
    }
};

/// Aggregated depth statistics for a JSONL file.
struct DepthStats {
    int64_t min_depth = 0;
    int64_t max_depth = 0;
    std::string root_event_id;
    double branching_factor = 0.0;
};

}  // namespace dag
