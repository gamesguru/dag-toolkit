#pragma once

#include <map>
#include <string>
#include <vector>

#include "event.hpp"
#include "server_report.hpp"

namespace dag {

/// Print the main comparison table header and rows.
void display_reports(const std::vector<ServerReport>& reports, bool rank,
                     bool verbose);

/// Print chain analysis results.
void display_chain_results(const std::vector<ChainResult>& results);

/// Write per-depth BF profile as CSV.
/// If output_path is empty, writes to stdout.
void write_profile_csv(const std::map<int64_t, DepthBucket>& profile,
                       const std::string& output_path = "");

}  // namespace dag
