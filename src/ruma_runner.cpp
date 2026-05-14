#include "ruma_runner.hpp"

#include <array>
#include <cstdio>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>

namespace dag {

std::optional<RumaSummary> run_ruma(const std::vector<std::string>& files,
                                    const std::string& version) {
    // Build command: ruma-lean -q -i f1 -i f2 ... --state-res <version> -f
    // summary
    std::ostringstream cmd;
    cmd << "ruma-lean -q";
    for (const auto& f : files) {
        cmd << " -i " << f;
    }
    cmd << " --state-res " << version << " -f summary 2>/dev/null";

    std::string cmd_str = cmd.str();

    // Execute and capture stdout
    auto pipe_deleter = [](FILE* f) {
        if (f) pclose(f);
    };
    std::unique_ptr<FILE, decltype(pipe_deleter)> pipe(
        popen(cmd_str.c_str(), "r"), pipe_deleter);

    if (!pipe) {
        return std::nullopt;
    }

    std::string output;
    std::array<char, 8192> buf;
    while (auto n = fread(buf.data(), 1, buf.size(), pipe.get())) {
        output.append(buf.data(), n);
    }

    int status = pclose(pipe.release());
    if (status != 0) {
        return std::nullopt;
    }

    if (output.empty()) {
        return std::nullopt;
    }

    // Parse with simdjson DOM (the output is a single JSON object, not
    // streaming)
    RumaSummary result;
    result.parser = std::make_unique<simdjson::dom::parser>();
    result.json_buf = std::make_unique<simdjson::padded_string>(output);

    auto doc = result.parser->parse(*result.json_buf);
    if (doc.error()) {
        return std::nullopt;
    }

    result.root = doc.value();
    return result;
}

std::vector<std::string> get_members(const simdjson::dom::element& summary,
                                     const std::string& category) {
    std::vector<std::string> users;
    try {
        auto membership = summary["membership"];
        auto cat = membership[category];
        auto user_array = cat["users"];

        for (auto user : user_array.get_array()) {
            auto uid = user["user_id"];
            std::string_view sv;
            if (!uid.get_string().get(sv)) {
                users.emplace_back(sv);
            }
        }
    } catch (...) {
        // Category doesn't exist or malformed — return empty.
    }
    return users;
}

std::map<std::string, std::vector<MemberEntry>> get_member_event_ids(
    const simdjson::dom::element& summary) {
    std::map<std::string, std::vector<MemberEntry>> result;
    const char* categories[] = {"join", "leave", "ban", "invite", "knock"};

    for (const auto* cat : categories) {
        std::vector<MemberEntry> entries;
        try {
            auto cat_data = summary["membership"][cat];
            auto user_array = cat_data["users"];
            for (auto user : user_array.get_array()) {
                std::string_view uid_sv, eid_sv;
                if (!user["user_id"].get_string().get(uid_sv) &&
                    !user["event_id"].get_string().get(eid_sv)) {
                    entries.push_back(
                        {std::string(uid_sv), std::string(eid_sv)});
                }
            }
        } catch (...) {
            // skip missing categories
        }
        result[cat] = std::move(entries);
    }
    return result;
}

}  // namespace dag
