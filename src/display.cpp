#include "display.hpp"

#include <algorithm>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>

#include "event.hpp"

namespace dag {

namespace {

std::string fmt_pct(double v) {
    char buf[16];
    std::snprintf(buf, sizeof(buf), "%5.1f%%", v * 100.0);
    return buf;
}

std::string fmt_bf(double v) {
    char buf[16];
    std::snprintf(buf, sizeof(buf), "%.3f", v);
    return buf;
}

std::string pad_right(const std::string& s, int width) {
    if (static_cast<int>(s.size()) >= width) return s;
    return s + std::string(width - s.size(), ' ');
}

std::string pad_left(const std::string& s, int width) {
    if (static_cast<int>(s.size()) >= width) return s;
    return std::string(width - s.size(), ' ') + s;
}

}  // namespace

void display_reports(const std::vector<ServerReport>& reports, bool rank,
                     bool verbose) {
    // Header
    std::ostringstream hdr;
    if (rank) {
        hdr << pad_right("#", 3) << " " << pad_right("SERVER", 26) << " "
            << pad_left("JOINED", 6) << " " << pad_left("LEFT", 5) << " "
            << pad_left("BAN", 4) << " " << pad_left("EVENTS", 6) << " "
            << pad_left("BF", 5) << " " << pad_left("PREC", 6) << " "
            << pad_left("RECALL", 6) << " " << pad_left("F1", 6) << " "
            << pad_left("DIFF", 10) << " " << pad_right("DEPTH", 14) << " ROOT";
    } else {
        hdr << pad_right("SERVER", 26) << " " << pad_left("JOINED", 6) << " "
            << pad_left("LEFT", 5) << " " << pad_left("BAN", 4) << " "
            << pad_left("EVENTS", 6) << " " << pad_left("BF", 5) << " "
            << pad_right("DEPTH", 14) << " " << pad_left("DIFF", 10) << " ROOT";
    }

    std::string header = hdr.str();
    std::cout << header << "\n";
    std::cout << std::string(header.size(), '-') << "\n";

    int idx = 1;
    for (const auto& r : reports) {
        // Depth range display
        int64_t depth_range = r.max_depth - r.min_depth + 1;
        std::string depth;
        if (depth_range > 0 && r.events > 0 &&
            static_cast<double>(r.events) / static_cast<double>(depth_range) <
                0.5) {
            depth = std::to_string(r.min_depth) + ".?." +
                    std::to_string(r.max_depth);
        } else {
            depth = std::to_string(r.min_depth) + ".." +
                    std::to_string(r.max_depth);
        }

        std::string bf_str = fmt_bf(r.bf);

        std::string j_str, l_str, b_str, prec_str, rec_str, f1_str, diff;

        if (r.res_joined == -1) {
            j_str = "ERR";
            l_str = "ERR";
            b_str = "ERR";
            prec_str = "  ERR";
            rec_str = "  ERR";
            f1_str = "  ERR";
            diff = "ERR";
        } else {
            j_str = std::to_string(r.res_joined);
            l_str = std::to_string(r.res_left);
            b_str = std::to_string(r.res_banned);
            prec_str = fmt_pct(r.precision);
            rec_str = fmt_pct(r.recall);
            f1_str = fmt_pct(r.f1);
            if (r.missing > 0 || r.extra > 0) {
                diff = "-" + std::to_string(r.missing) + "/+" +
                       std::to_string(r.extra);
            } else {
                diff = "\xe2\x9c\x93";  // UTF-8 checkmark ✓
            }
        }

        if (rank) {
            std::cout << pad_right(std::to_string(idx), 3) << " "
                      << pad_right(r.server, 26) << " " << pad_left(j_str, 6)
                      << " " << pad_left(l_str, 5) << " " << pad_left(b_str, 4)
                      << " " << pad_left(std::to_string(r.events), 6) << " "
                      << pad_left(bf_str, 5) << " " << pad_left(prec_str, 6)
                      << " " << pad_left(rec_str, 6) << " "
                      << pad_left(f1_str, 6) << " " << pad_left(diff, 10) << " "
                      << pad_right(depth, 14) << " " << r.root << "\n";
        } else {
            std::cout << pad_right(r.server, 26) << " " << pad_left(j_str, 6)
                      << " " << pad_left(l_str, 5) << " " << pad_left(b_str, 4)
                      << " " << pad_left(std::to_string(r.events), 6) << " "
                      << pad_left(bf_str, 5) << " " << pad_right(depth, 14)
                      << " " << pad_left(diff, 10) << " " << r.root << "\n";
        }

        if (verbose && (r.missing > 0 || r.extra > 0)) {
            if (!r.missing_users.empty()) {
                std::cout << "  missing:\n";
                for (const auto& u : r.missing_users) {
                    std::cout << "    - " << u << "\n";
                }
            }
            if (!r.extra_users.empty()) {
                std::cout << "  extra:\n";
                for (const auto& u : r.extra_users) {
                    std::cout << "    + " << u << "\n";
                }
            }
        }

        ++idx;
    }
}

void display_chain_results(const std::vector<ChainResult>& results) {
    // Count references
    std::map<std::string, int> ref_counts;
    for (const auto& cr : results) {
        for (const auto& p : cr.partners) {
            ref_counts[p]++;
        }
    }

    // Header
    std::string hdr = pad_right("SERVER", 26) + " " + pad_left("JOINED", 6) +
                      " " + pad_left("LEFT", 5) + " " + pad_left("BAN", 4) +
                      " " + pad_left("CHAIN", 5) + " " + pad_left("REFS", 4) +
                      " PARTNERS";

    std::cout << "\nChain Analysis:\n";
    std::cout << hdr << "\n";
    std::cout << std::string(80, '-') << "\n";

    for (const auto& cr : results) {
        int refs = 0;
        auto it = ref_counts.find(cr.server);
        if (it != ref_counts.end()) {
            refs = it->second;
        }

        std::string partners;
        if (cr.partners.empty()) {
            partners = "(solo)";
        } else {
            for (size_t i = 0; i < cr.partners.size(); ++i) {
                if (i > 0) partners += "+";
                partners += cr.partners[i];
            }
        }

        std::cout << pad_right(cr.server, 26) << " "
                  << pad_left(std::to_string(cr.joined), 6) << " "
                  << pad_left(std::to_string(cr.left), 5) << " "
                  << pad_left(std::to_string(cr.ban), 4) << " "
                  << pad_left(std::to_string(cr.chain_len), 5) << " "
                  << pad_left(std::to_string(refs), 4) << " " << partners
                  << "\n";
    }

    // Strongest links summary
    if (!ref_counts.empty()) {
        std::vector<std::pair<std::string, int>> top(ref_counts.begin(),
                                                     ref_counts.end());
        std::sort(top.begin(), top.end(), [](const auto& a, const auto& b) {
            return a.second > b.second;
        });

        int n = static_cast<int>(results.size());
        std::cout << "\n    strongest links:\n";

        auto top_end = top.begin() + std::min<ptrdiff_t>(5, top.size());
        size_t max_srv = std::accumulate(
            top.begin(), top_end, size_t{0}, [](size_t acc, const auto& p) {
                return std::max(acc, p.first.size());
            });

        for (size_t i = 0; i < std::min<size_t>(5, top.size()); ++i) {
            std::cout << "      "
                      << pad_right(top[i].first + ":",
                                   static_cast<int>(max_srv + 1))
                      << " " << pad_left(std::to_string(top[i].second), 2)
                      << "/" << n << " chains\n";
        }
    }
}

void write_profile_csv(const std::map<int64_t, DepthBucket>& profile,
                       const std::string& output_path) {
    std::ostream* out = &std::cout;
    std::ofstream file;
    if (!output_path.empty()) {
        file.open(output_path);
        if (!file.is_open()) {
            std::cerr << "Failed to open: " << output_path << "\n";
            return;
        }
        out = &file;
    }

    *out << "depth,events,prev_events,bf\n";
    for (const auto& [depth, bucket] : profile) {
        char bf_buf[16];
        std::snprintf(bf_buf, sizeof(bf_buf), "%.3f", bucket.bf());
        *out << depth << "," << bucket.event_count << ","
             << bucket.total_prev_events << "," << bf_buf << "\n";
    }
}

}  // namespace dag
