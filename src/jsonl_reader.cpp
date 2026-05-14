#include "jsonl_reader.hpp"

#include <simdjson.h>

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace dag {

namespace {

// simdjson requires padded strings for ondemand parsing.
// We read each line, then parse with padded_string.

std::string read_string_field(simdjson::ondemand::object& obj, std::string_view key) {
    auto val = obj.find_field_unordered(key);
    if (val.error()) return {};
    std::string_view sv;
    if (val.get_string().get(sv)) return {};
    return std::string(sv);
}

int64_t read_int_field(simdjson::ondemand::object& obj, std::string_view key) {
    auto val = obj.find_field_unordered(key);
    if (val.error()) return 0;
    int64_t v = 0;
    if (val.get_int64().get(v)) return 0;
    return v;
}

std::vector<std::string> read_string_array(simdjson::ondemand::object& obj, std::string_view key) {
    std::vector<std::string> result;
    auto arr = obj.find_field_unordered(key);
    if (arr.error()) return result;
    simdjson::ondemand::array a;
    if (arr.get_array().get(a)) return result;
    for (auto elem : a) {
        std::string_view sv;
        if (!elem.get_string().get(sv)) {
            result.emplace_back(sv);
        }
    }
    return result;
}

}  // namespace

std::unordered_set<std::string> load_event_ids(const std::string& path) {
    std::unordered_set<std::string> ids;
    simdjson::ondemand::parser parser;

    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "Failed to open: " << path << "\n";
        return ids;
    }

    std::string line;
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        simdjson::padded_string padded(line);
        auto doc = parser.iterate(padded);
        if (doc.error()) continue;

        simdjson::ondemand::object obj;
        if (doc.get_object().get(obj)) continue;

        auto eid = obj.find_field_unordered("event_id");
        if (eid.error()) continue;
        std::string_view sv;
        if (eid.get_string().get(sv)) continue;
        ids.emplace(sv);
    }
    return ids;
}

std::vector<Event> load_events(const std::string& path) {
    std::vector<Event> events;
    simdjson::ondemand::parser parser;

    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "Failed to open: " << path << "\n";
        return events;
    }

    std::string line;
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        simdjson::padded_string padded(line);
        auto doc = parser.iterate(padded);
        if (doc.error()) continue;

        simdjson::ondemand::object obj;
        if (doc.get_object().get(obj)) continue;

        Event ev;
        ev.event_id = read_string_field(obj, "event_id");
        ev.room_id = read_string_field(obj, "room_id");
        ev.sender = read_string_field(obj, "sender");
        ev.type = read_string_field(obj, "type");
        ev.state_key = read_string_field(obj, "state_key");
        ev.depth = read_int_field(obj, "depth");
        ev.origin_server_ts = read_int_field(obj, "origin_server_ts");
        ev.prev_events = read_string_array(obj, "prev_events");
        ev.auth_events = read_string_array(obj, "auth_events");

        // Extract membership from content.membership for member events
        if (ev.type == "m.room.member") {
            auto content = obj.find_field_unordered("content");
            if (!content.error()) {
                simdjson::ondemand::object cobj;
                if (!content.get_object().get(cobj)) {
                    ev.membership = read_string_field(cobj, "membership");
                }
            }
        }

        if (!ev.event_id.empty()) {
            events.push_back(std::move(ev));
        }
    }
    return events;
}

DepthStats get_depth_stats(const std::string& path) {
    DepthStats stats;
    simdjson::ondemand::parser parser;

    std::ifstream file(path);
    if (!file.is_open()) return stats;

    int64_t min_d = INT64_MAX;
    int64_t max_d = 0;
    int64_t total_prev = 0;
    int64_t n_events = 0;

    std::string line;
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        simdjson::padded_string padded(line);
        auto doc = parser.iterate(padded);
        if (doc.error()) continue;

        simdjson::ondemand::object obj;
        if (doc.get_object().get(obj)) continue;

        int64_t d = read_int_field(obj, "depth");

        if (d < min_d) {
            min_d = d;
            stats.root_event_id = read_string_field(obj, "event_id");
        }
        if (d > max_d) {
            max_d = d;
        }

        // Count prev_events
        auto arr = obj.find_field_unordered("prev_events");
        if (!arr.error()) {
            simdjson::ondemand::array a;
            if (!arr.get_array().get(a)) {
                for ([[maybe_unused]] auto elem : a) {
                    ++total_prev;
                }
            }
        }

        ++n_events;
    }

    stats.min_depth = (min_d == INT64_MAX) ? 0 : min_d;
    stats.max_depth = max_d;
    stats.branching_factor = n_events > 0
        ? static_cast<double>(total_prev) / static_cast<double>(n_events)
        : 0.0;

    return stats;
}

std::map<int64_t, DepthBucket> get_depth_profile(const std::string& path) {
    std::map<int64_t, DepthBucket> profile;
    simdjson::ondemand::parser parser;

    std::ifstream file(path);
    if (!file.is_open()) return profile;

    std::string line;
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        simdjson::padded_string padded(line);
        auto doc = parser.iterate(padded);
        if (doc.error()) continue;

        simdjson::ondemand::object obj;
        if (doc.get_object().get(obj)) continue;

        int64_t d = read_int_field(obj, "depth");

        int64_t prev_count = 0;
        auto arr = obj.find_field_unordered("prev_events");
        if (!arr.error()) {
            simdjson::ondemand::array a;
            if (!arr.get_array().get(a)) {
                for ([[maybe_unused]] auto elem : a) {
                    ++prev_count;
                }
            }
        }

        auto& bucket = profile[d];
        bucket.event_count++;
        bucket.total_prev_events += prev_count;
    }

    return profile;
}

std::map<int64_t, DepthBucket> get_merged_depth_profile(
    const std::vector<std::string>& paths) {
    std::map<int64_t, DepthBucket> merged;
    for (const auto& p : paths) {
        auto profile = get_depth_profile(p);
        for (const auto& [depth, bucket] : profile) {
            auto& m = merged[depth];
            m.event_count += bucket.event_count;
            m.total_prev_events += bucket.total_prev_events;
        }
    }
    return merged;
}

}  // namespace dag
