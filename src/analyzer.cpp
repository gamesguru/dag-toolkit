#include "analyzer.hpp"

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <future>
#include <iostream>
#include <map>
#include <regex>
#include <unordered_set>
#include <vector>

#include "display.hpp"
#include "event.hpp"
#include "jsonl_reader.hpp"
#include "ruma_runner.hpp"
#include "server_report.hpp"

namespace fs = std::filesystem;

namespace dag {

namespace {

/// Glob for files matching pattern in the given directory.
std::vector<fs::path> glob_files(const std::string& dir,
                                 const std::string& prefix,
                                 const std::string& room) {
    std::vector<fs::path> matches;
    std::string pat_prefix = prefix + "-" + room + "-";

    for (const auto& entry : fs::directory_iterator(dir)) {
        if (!entry.is_regular_file()) continue;
        auto fname = entry.path().filename().string();
        if (fname.starts_with(pat_prefix) && fname.ends_with(".jsonl")) {
            matches.push_back(entry.path());
        }
    }

    std::sort(matches.begin(), matches.end());
    return matches;
}

/// Strip domain suffixes: iteratively remove trailing -<digits>, -tip,
/// -<short-alpha> etc.
std::string strip_domain_suffix(const std::string& server) {
    static const std::regex suffix_re(R"(-((\d+|tip|[a-z]{1,4}\d*))$)");
    std::string base = server;
    std::string prev;
    while (base != prev) {
        prev = base;
        base = std::regex_replace(base, suffix_re, "");
    }
    return base;
}

/// Build a set from a vector for fast lookups.
std::unordered_set<std::string> to_set(const std::vector<std::string>& v) {
    return {v.begin(), v.end()};
}

/// Set intersection.
std::unordered_set<std::string> set_intersect(
    const std::unordered_set<std::string>& a,
    const std::unordered_set<std::string>& b) {
    std::unordered_set<std::string> result;
    for (const auto& x : a) {
        if (b.count(x)) result.insert(x);
    }
    return result;
}

/// Set difference: a - b.
std::vector<std::string> set_diff_sorted(
    const std::unordered_set<std::string>& a,
    const std::unordered_set<std::string>& b) {
    std::vector<std::string> result;
    std::copy_if(a.begin(), a.end(), std::back_inserter(result),
                 [&b](const std::string& x) { return !b.count(x); });
    std::sort(result.begin(), result.end());
    return result;
}

}  // namespace

void analyze(const std::string& room, const std::string& prefix, bool verbose,
             bool rank, bool chain_analysis, const std::string& version,
             const std::string& workdir) {
    auto files = glob_files(workdir, prefix, room);
    if (files.empty()) {
        std::cerr << "No files matching " << prefix << "-" << room
                  << "-*.jsonl\n";
        std::exit(1);
    }

    // Group files by base domain
    std::map<std::string, std::vector<fs::path>> domain_files;
    for (const auto& f : files) {
        std::string fname = f.filename().string();
        std::string server = fname.substr((prefix + "-" + room + "-").size());
        // Strip extension at last '.'
        if (auto dot = server.rfind('.'); dot != std::string::npos) {
            server.resize(dot);
        }
        std::string base = strip_domain_suffix(server);
        domain_files[base].push_back(f);
    }

    // Ground truth: merge all files
    std::vector<std::string> file_strs;
    file_strs.reserve(files.size());
    std::transform(files.begin(), files.end(), std::back_inserter(file_strs),
                   [](const fs::path& p) { return p.string(); });

    std::cerr << "Merging " << files.size() << " server DAGs...\n";
    auto gt = run_ruma(file_strs, version);
    if (!gt) {
        std::cerr << "Failed to compute ground truth\n";
        std::exit(1);
    }

    auto gt_members_vec = get_members(gt->root, "join");
    auto gt_members = to_set(gt_members_vec);
    auto gt_member_eids = get_member_event_ids(gt->root);
    auto gt_n = gt_members.size();

    int64_t gt_left = 0, gt_banned = 0;
    if (auto it = gt_member_eids.find("leave"); it != gt_member_eids.end()) {
        gt_left = static_cast<int64_t>(it->second.size());
    }
    if (auto it = gt_member_eids.find("ban"); it != gt_member_eids.end()) {
        gt_banned = static_cast<int64_t>(it->second.size());
    }

    // Get ground truth stats from the summary
    int64_t gt_events = 0, gt_min = 0, gt_max = 0;
    std::string gt_root;
    try {
        gt_events = gt->root["total_events"].get_int64().value();
    } catch (...) {
    }
    try {
        gt_min = gt->root["min_depth"].get_int64().value();
    } catch (...) {
    }
    try {
        gt_max = gt->root["max_depth"].get_int64().value();
    } catch (...) {
    }
    try {
        std::string_view sv = gt->root["root_event_id"].get_string().value();
        gt_root = std::string(sv);
    } catch (...) {
    }

    std::cout << "ground truth: " << gt_n << " joined, " << gt_left << " left, "
              << gt_banned << " banned, " << gt_events << " events, "
              << "depth " << gt_min << ".." << gt_max << ", root " << gt_root
              << "\n\n";

    // Per-domain analysis (parallelized)
    std::vector<std::pair<std::string, std::vector<fs::path>>> domain_list(
        domain_files.begin(), domain_files.end());

    std::vector<std::future<ServerReport>> futures;
    futures.reserve(domain_list.size());

    for (const auto& [domain, dfiles] : domain_list) {
        futures.push_back(std::async(
            std::launch::async,
            [&gt_members, gt_n, &version](
                const std::string& domain,
                const std::vector<fs::path>& dfiles) -> ServerReport {
                std::unordered_set<std::string> srv_eids;
                int64_t min_d = INT64_MAX;
                int64_t max_d = 0;
                std::string root_id;
                int64_t total_prev = 0;
                int64_t n_events = 0;

                for (const auto& f : dfiles) {
                    auto fstr = f.string();
                    auto ids = load_event_ids(fstr);
                    srv_eids.insert(ids.begin(), ids.end());

                    auto stats = get_depth_stats(fstr);
                    if (stats.min_depth < min_d) {
                        min_d = stats.min_depth;
                        root_id = stats.root_event_id;
                    }
                    if (stats.max_depth > max_d) {
                        max_d = stats.max_depth;
                    }

                    auto n_f = static_cast<int64_t>(ids.size());
                    total_prev += static_cast<int64_t>(
                        stats.branching_factor * static_cast<double>(n_f));
                    n_events += n_f;
                }

                ServerReport r;
                r.server = domain;
                r.events = static_cast<int64_t>(srv_eids.size());
                r.min_depth = (min_d == INT64_MAX) ? 0 : min_d;
                r.max_depth = max_d;
                r.root = root_id;
                r.bf = n_events > 0 ? static_cast<double>(total_prev) /
                                          static_cast<double>(n_events)
                                    : 0.0;

                // State-res on this domain's files
                std::vector<std::string> domain_file_strs;
                std::transform(dfiles.begin(), dfiles.end(),
                               std::back_inserter(domain_file_strs),
                               [](const fs::path& p) { return p.string(); });

                auto srv_summary = run_ruma(domain_file_strs, version);
                std::unordered_set<std::string> srv_own_members;

                if (!srv_summary) {
                    r.res_joined = -1;
                    r.res_left = -1;
                    r.res_banned = -1;
                } else {
                    auto members = get_members(srv_summary->root, "join");
                    srv_own_members = to_set(members);
                    r.res_joined = static_cast<int64_t>(srv_own_members.size());

                    auto leave_m = get_members(srv_summary->root, "leave");
                    r.res_left = static_cast<int64_t>(leave_m.size());

                    auto ban_m = get_members(srv_summary->root, "ban");
                    r.res_banned = static_cast<int64_t>(ban_m.size());
                }

                r.missing_users = set_diff_sorted(gt_members, srv_own_members);
                r.extra_users = set_diff_sorted(srv_own_members, gt_members);
                r.missing = static_cast<int64_t>(r.missing_users.size());
                r.extra = static_cast<int64_t>(r.extra_users.size());

                auto tp = set_intersect(gt_members, srv_own_members).size();
                r.precision =
                    srv_own_members.empty()
                        ? 0.0
                        : static_cast<double>(tp) /
                              static_cast<double>(srv_own_members.size());
                r.recall = gt_n == 0 ? 0.0
                                     : static_cast<double>(tp) /
                                           static_cast<double>(gt_n);
                r.f1 = (r.precision + r.recall) > 0.0
                           ? 2.0 * r.precision * r.recall /
                                 (r.precision + r.recall)
                           : 0.0;

                return r;
            },
            domain, dfiles));
    }

    // Collect results
    std::vector<ServerReport> reports;
    reports.reserve(futures.size());
    std::transform(futures.begin(), futures.end(), std::back_inserter(reports),
                   [](std::future<ServerReport>& f) { return f.get(); });

    // Sort by F1 if ranking
    if (rank) {
        std::sort(reports.begin(), reports.end(),
                  [](const ServerReport& a, const ServerReport& b) {
                      return a.f1 > b.f1;
                  });
    }

    display_reports(reports, rank, verbose);

    // Chain analysis
    if (!chain_analysis) return;

    // Build per-domain event ID sets for chain analysis
    std::map<std::string, std::unordered_set<std::string>> domain_eids;
    for (const auto& [domain, dfiles] : domain_files) {
        for (const auto& f : dfiles) {
            auto ids = load_event_ids(f.string());
            domain_eids[domain].insert(ids.begin(), ids.end());
        }
    }

    // Target all state event IDs
    std::unordered_set<std::string> target_eids;
    for (const auto* cat : {"join", "leave", "ban", "invite"}) {
        if (auto it = gt_member_eids.find(cat); it != gt_member_eids.end()) {
            for (const auto& entry : it->second) {
                target_eids.insert(entry.event_id);
            }
        }
    }

    std::vector<ChainResult> chain_results;

    std::vector<std::string> sorted_domains;
    for (const auto& [d, _] : domain_files) {
        sorted_domains.push_back(d);
    }
    std::sort(sorted_domains.begin(), sorted_domains.end());

    for (const auto& start : sorted_domains) {
        std::vector<std::string> current_chain = {start};
        auto covered = set_intersect(domain_eids[start], target_eids);
        auto uncovered_set = target_eids;
        for (const auto& x : covered) {
            uncovered_set.erase(x);
        }

        while (!uncovered_set.empty()) {
            std::string best;
            std::unordered_set<std::string> best_added;

            for (const auto& [candidate, c_eids] : domain_eids) {
                // Skip if already in chain
                if (std::find(current_chain.begin(), current_chain.end(),
                              candidate) != current_chain.end()) {
                    continue;
                }

                auto added = set_intersect(uncovered_set, c_eids);
                if (added.size() > best_added.size()) {
                    best = candidate;
                    best_added = added;
                }
            }

            if (best.empty()) break;

            current_chain.push_back(best);
            covered.insert(best_added.begin(), best_added.end());
            for (const auto& x : best_added) {
                uncovered_set.erase(x);
            }
        }

        // State-res on the chain
        std::vector<std::string> chain_files;
        for (const auto& d : current_chain) {
            const auto& df = domain_files[d];
            std::transform(df.begin(), df.end(),
                           std::back_inserter(chain_files),
                           [](const fs::path& p) { return p.string(); });
        }

        auto summary = run_ruma(chain_files, version);
        if (!summary) continue;

        ChainResult cr;
        cr.server = start;
        cr.joined =
            static_cast<int64_t>(get_members(summary->root, "join").size());
        cr.left =
            static_cast<int64_t>(get_members(summary->root, "leave").size());
        cr.ban = static_cast<int64_t>(get_members(summary->root, "ban").size());
        cr.chain_len = static_cast<int64_t>(current_chain.size());
        cr.partners.assign(current_chain.begin() + 1, current_chain.end());

        chain_results.push_back(std::move(cr));
    }

    display_chain_results(chain_results);
}

void profile(const std::string& room, const std::string& prefix,
             const std::string& output_path, const std::string& workdir) {
    auto files = glob_files(workdir, prefix, room);
    if (files.empty()) {
        std::cerr << "No files matching " << prefix << "-" << room
                  << "-*.jsonl\n";
        std::exit(1);
    }

    std::vector<std::string> paths;
    paths.reserve(files.size());
    std::transform(files.begin(), files.end(), std::back_inserter(paths),
                   [](const fs::path& p) { return p.string(); });

    profile_files(paths, output_path);
}

void profile_files(const std::vector<std::string>& files,
                   const std::string& output_path) {
    std::cerr << "Profiling " << files.size() << " file(s)...\n";

    auto merged = get_merged_depth_profile(files);
    write_profile_csv(merged, output_path);

    // Summary stats
    int64_t total_events = 0;
    double max_bf = 0.0;
    int64_t max_bf_depth = 0;
    int64_t storm_depths = 0;  // depths with BF > 2.0

    for (const auto& [depth, bucket] : merged) {
        total_events += bucket.event_count;
        double bf = bucket.bf();
        if (bf > max_bf) {
            max_bf = bf;
            max_bf_depth = depth;
        }
        if (bf > 2.0) {
            ++storm_depths;
        }
    }

    double avg_bf = 0.0;
    if (!merged.empty()) {
        int64_t total_prev = 0;
        for (const auto& [_, bucket] : merged) {
            total_prev += bucket.total_prev_events;
        }
        avg_bf =
            static_cast<double>(total_prev) / static_cast<double>(total_events);
    }

    char bf_buf[16];
    std::snprintf(bf_buf, sizeof(bf_buf), "%.3f", avg_bf);
    char max_bf_buf[16];
    std::snprintf(max_bf_buf, sizeof(max_bf_buf), "%.3f", max_bf);

    std::cerr << "  depths: " << merged.size() << ", events: " << total_events
              << ", avg BF: " << bf_buf << ", peak BF: " << max_bf_buf
              << " @ depth " << max_bf_depth
              << ", storm depths (>2.0): " << storm_depths << "\n";
}

void analyze_files(const std::vector<std::string>& files, bool verbose,
                   bool rank, bool chain_analysis, const std::string& version) {
    if (files.empty()) {
        std::cerr << "No input files\n";
        std::exit(1);
    }

    // Derive domain grouping from filenames
    std::map<std::string, std::vector<fs::path>> domain_files;
    for (const auto& f : files) {
        fs::path p(f);
        std::string stem = p.stem().string();
        std::string base = strip_domain_suffix(stem);
        domain_files[base].push_back(p);
    }

    // Ground truth: merge all files
    std::cerr << "Merging " << files.size() << " server DAGs...\n";
    auto gt = run_ruma(files, version);
    if (!gt) {
        // Single merged file: show profile stats without comparison
        if (files.size() == 1) {
            auto stats = get_depth_stats(files[0]);
            auto ids = load_event_ids(files[0]);
            std::cout << "Summary: " << ids.size() << " events, "
                      << "depth " << stats.min_depth << ".." << stats.max_depth
                      << ", BF " << std::fixed << std::setprecision(3)
                      << stats.branching_factor << ", root "
                      << stats.root_event_id << "\n";
            std::cout << "\nTip: use --profile for per-depth BF data, "
                      << "or viz/dagstorms.py for storm detection.\n";
            return;
        }
        std::cerr << "Failed to compute ground truth\n";
        std::exit(1);
    }

    auto gt_members_vec = get_members(gt->root, "join");
    auto gt_members = to_set(gt_members_vec);
    auto gt_member_eids = get_member_event_ids(gt->root);
    auto gt_n = gt_members.size();

    int64_t gt_left = 0, gt_banned = 0;
    if (auto it = gt_member_eids.find("leave"); it != gt_member_eids.end()) {
        gt_left = static_cast<int64_t>(it->second.size());
    }
    if (auto it = gt_member_eids.find("ban"); it != gt_member_eids.end()) {
        gt_banned = static_cast<int64_t>(it->second.size());
    }

    int64_t gt_events = 0, gt_min = 0, gt_max = 0;
    std::string gt_root;
    try {
        gt_events = gt->root["total_events"].get_int64().value();
    } catch (...) {
    }
    try {
        gt_min = gt->root["min_depth"].get_int64().value();
    } catch (...) {
    }
    try {
        gt_max = gt->root["max_depth"].get_int64().value();
    } catch (...) {
    }
    try {
        std::string_view sv = gt->root["root_event_id"].get_string().value();
        gt_root = std::string(sv);
    } catch (...) {
    }

    std::cout << "ground truth: " << gt_n << " joined, " << gt_left << " left, "
              << gt_banned << " banned, " << gt_events << " events, "
              << "depth " << gt_min << ".." << gt_max << ", root " << gt_root
              << "\n\n";

    // Per-domain analysis (same as analyze())
    std::vector<ServerReport> reports;

    for (const auto& [domain, dfiles] : domain_files) {
        std::unordered_set<std::string> srv_eids;
        int64_t min_d = INT64_MAX;
        int64_t max_d = 0;
        std::string root_id;
        int64_t total_prev = 0;
        int64_t n_events = 0;

        for (const auto& f : dfiles) {
            auto fstr = f.string();
            auto ids = load_event_ids(fstr);
            srv_eids.insert(ids.begin(), ids.end());

            auto stats = get_depth_stats(fstr);
            if (stats.min_depth < min_d) {
                min_d = stats.min_depth;
                root_id = stats.root_event_id;
            }
            if (stats.max_depth > max_d) {
                max_d = stats.max_depth;
            }

            auto n_f = static_cast<int64_t>(ids.size());
            total_prev += static_cast<int64_t>(stats.branching_factor *
                                               static_cast<double>(n_f));
            n_events += n_f;
        }

        ServerReport r;
        r.server = domain;
        r.events = static_cast<int64_t>(srv_eids.size());
        r.min_depth = (min_d == INT64_MAX) ? 0 : min_d;
        r.max_depth = max_d;
        r.root = root_id;
        r.bf = n_events > 0 ? static_cast<double>(total_prev) /
                                  static_cast<double>(n_events)
                            : 0.0;

        std::vector<std::string> domain_file_strs;
        std::transform(dfiles.begin(), dfiles.end(),
                       std::back_inserter(domain_file_strs),
                       [](const fs::path& p) { return p.string(); });

        auto srv_summary = run_ruma(domain_file_strs, version);
        std::unordered_set<std::string> srv_own_members;

        if (!srv_summary) {
            r.res_joined = -1;
            r.res_left = -1;
            r.res_banned = -1;
        } else {
            auto members = get_members(srv_summary->root, "join");
            srv_own_members = to_set(members);
            r.res_joined = static_cast<int64_t>(srv_own_members.size());
            r.res_left = static_cast<int64_t>(
                get_members(srv_summary->root, "leave").size());
            r.res_banned = static_cast<int64_t>(
                get_members(srv_summary->root, "ban").size());
        }

        r.missing_users = set_diff_sorted(gt_members, srv_own_members);
        r.extra_users = set_diff_sorted(srv_own_members, gt_members);
        r.missing = static_cast<int64_t>(r.missing_users.size());
        r.extra = static_cast<int64_t>(r.extra_users.size());

        auto tp = set_intersect(gt_members, srv_own_members).size();
        r.precision = srv_own_members.empty()
                          ? 0.0
                          : static_cast<double>(tp) /
                                static_cast<double>(srv_own_members.size());
        r.recall = gt_n == 0
                       ? 0.0
                       : static_cast<double>(tp) / static_cast<double>(gt_n);
        r.f1 = (r.precision + r.recall) > 0.0
                   ? 2.0 * r.precision * r.recall / (r.precision + r.recall)
                   : 0.0;

        reports.push_back(std::move(r));
    }

    if (rank) {
        std::sort(reports.begin(), reports.end(),
                  [](const ServerReport& a, const ServerReport& b) {
                      return a.f1 > b.f1;
                  });
    }

    display_reports(reports, rank, verbose);
}

}  // namespace dag
