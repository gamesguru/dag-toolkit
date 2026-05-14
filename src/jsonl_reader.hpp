#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <unordered_set>
#include <vector>

#include "event.hpp"

namespace dag {

/// Load all event_ids from a JSONL file.
[[nodiscard]] std::unordered_set<std::string> load_event_ids(
    const std::string& path);

/// Load all events from a JSONL file (full parse).
[[nodiscard]] std::vector<Event> load_events(const std::string& path);

/// Get min_depth, max_depth, root_event_id, and branching factor in a single
/// pass.
[[nodiscard]] DepthStats get_depth_stats(const std::string& path);

/// Get per-depth branching factor profile. Key = depth, Value = bucket with
/// event count and total prev_events at that depth.
[[nodiscard]] std::map<int64_t, DepthBucket> get_depth_profile(
    const std::string& path);

/// Merged profile across multiple files.
[[nodiscard]] std::map<int64_t, DepthBucket> get_merged_depth_profile(
    const std::vector<std::string>& paths);

}  // namespace dag
