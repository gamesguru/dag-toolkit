#include <algorithm>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include "analyzer.hpp"

static void usage(const char* prog) {
    std::cerr
        << "Federated DAG comparison and profiling tool.\n\n"
        << "Usage:\n"
        << "  " << prog << " <room-slug> [options]\n"
        << "  " << prog << " -i <file1> [-i <file2> ...] [options]\n\n"
        << "Options:\n"
        << "  -i, --input FILE  Input JSONL file (repeatable)\n"
        << "  -d, --dir DIR    Working directory for JSONL files (default: .)\n"
        << "  --prefix PREFIX   JSONL file prefix (default: remote-dag)\n"
        << "  -v, --verbose     Show per-user diffs\n"
        << "  -r, --rank        Rank by F1 score\n"
        << "  -c, --chain       Greedy chain analysis\n"
        << "  --profile [FILE]  Emit per-depth BF profile as CSV\n"
        << "                    If FILE is given, write to file; else stdout\n"
        << "  --version VER     State-res version (default: v2-1)\n"
        << "  -h, --help        Show this help\n";
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        usage(argv[0]);
        return 1;
    }

    std::string room;
    std::string prefix = "remote-dag";
    std::string version = "v2-1";
    bool verbose = false;
    bool rank = false;
    bool chain = false;
    bool do_profile = false;
    std::string profile_path;
    std::string workdir = ".";
    std::vector<std::string> input_files;

    // First positional arg is room (if not a flag)
    int i = 1;
    if (argv[1][0] != '-') {
        room = argv[1];
        i = 2;
    }

    for (; i < argc; ++i) {
        // Expand combined short flags (e.g. -cr -> -c -r)
        std::string arg = argv[i];
        if (arg.size() > 2 && arg[0] == '-' && arg[1] != '-') {
            static const std::string bool_flags = "vrch";
            bool all_bool =
                std::all_of(arg.begin() + 1, arg.end(), [&](char c) {
                    return bool_flags.find(c) != std::string::npos;
                });
            if (all_bool) {
                for (size_t j = 1; j < arg.size(); ++j) {
                    switch (arg[j]) {
                        case 'v':
                            verbose = true;
                            break;
                        case 'r':
                            rank = true;
                            break;
                        case 'c':
                            chain = true;
                            break;
                        case 'h':
                            usage(argv[0]);
                            return 0;
                    }
                }
                continue;
            }
        }

        if ((std::strcmp(argv[i], "-i") == 0 ||
             std::strcmp(argv[i], "--input") == 0) &&
            i + 1 < argc) {
            input_files.emplace_back(argv[++i]);
        } else if ((std::strcmp(argv[i], "-d") == 0 ||
                    std::strcmp(argv[i], "--dir") == 0) &&
                   i + 1 < argc) {
            workdir = argv[++i];
        } else if (std::strcmp(argv[i], "--prefix") == 0 && i + 1 < argc) {
            prefix = argv[++i];
        } else if (std::strcmp(argv[i], "-v") == 0 ||
                   std::strcmp(argv[i], "--verbose") == 0) {
            verbose = true;
        } else if (std::strcmp(argv[i], "-r") == 0 ||
                   std::strcmp(argv[i], "--rank") == 0) {
            rank = true;
        } else if (std::strcmp(argv[i], "-c") == 0 ||
                   std::strcmp(argv[i], "--chain") == 0) {
            chain = true;
        } else if (std::strcmp(argv[i], "--profile") == 0) {
            do_profile = true;
            // Optional file argument: next arg if it doesn't start with -
            if (i + 1 < argc && argv[i + 1][0] != '-') {
                profile_path = argv[++i];
            }
        } else if (std::strcmp(argv[i], "--version") == 0 && i + 1 < argc) {
            version = argv[++i];
        } else if (std::strcmp(argv[i], "-h") == 0 ||
                   std::strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else if (room.empty() && argv[i][0] != '-') {
            room = argv[i];
        } else {
            std::cerr << "Unknown option: " << argv[i] << "\n";
            usage(argv[0]);
            return 1;
        }
    }

    // Direct file mode: -i takes precedence over room slug
    if (!input_files.empty()) {
        if (do_profile) {
            dag::profile_files(input_files, profile_path);
        } else {
            dag::analyze_files(input_files, verbose, rank, chain, version);
        }
        return 0;
    }

    if (room.empty()) {
        std::cerr << "Error: room slug or -i <file> is required\n";
        usage(argv[0]);
        return 1;
    }

    if (do_profile) {
        dag::profile(room, prefix, profile_path, workdir);
    } else {
        dag::analyze(room, prefix, verbose, rank, chain, version, workdir);
    }

    return 0;
}
