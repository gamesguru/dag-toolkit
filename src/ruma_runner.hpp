#pragma once

#include <optional>
#include <string>
#include <vector>

// We use simdjson for parsing ruma-lean's JSON output too.
#include <simdjson.h>

namespace dag {

/// Result of running ruma-lean state resolution.
/// Contains the raw JSON summary as a parsed DOM document.
struct RumaSummary {
    simdjson::dom::element root;
    // The parser + document must outlive the element references.
    std::unique_ptr<simdjson::dom::parser> parser;
    std::unique_ptr<simdjson::padded_string> json_buf;
};

/// Run ruma-lean on the given JSONL files and return the parsed JSON summary.
/// Returns nullopt on failure (non-zero exit, bad JSON, etc).
[[nodiscard]] std::optional<RumaSummary> run_ruma(
    const std::vector<std::string>& files,
    const std::string& version = "v2-1");

/// Extract user IDs belonging to a membership category from a ruma-lean summary.
/// category: "join", "leave", "ban", "invite", "knock"
[[nodiscard]] std::vector<std::string> get_members(
    const simdjson::dom::element& summary,
    const std::string& category);

/// Extract member event_ids: category -> {user_id: event_id}
struct MemberEntry {
    std::string user_id;
    std::string event_id;
};

[[nodiscard]] std::map<std::string, std::vector<MemberEntry>> get_member_event_ids(
    const simdjson::dom::element& summary);

}  // namespace dag
