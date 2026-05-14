#pragma once

#include <string>

namespace dag {

/// Run the full comparison analysis (mirrors Python's analyze() function).
void analyze(const std::string& room, const std::string& prefix, bool verbose,
             bool rank, bool chain_analysis,
             const std::string& version = "v2-1");

/// Run depth-profile mode: emit per-depth BF data as CSV.
/// If output_path is empty, writes to stdout.
void profile(const std::string& room, const std::string& prefix,
             const std::string& output_path = "",
             const std::string& workdir = ".");

}  // namespace dag
