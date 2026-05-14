#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include "analyzer.hpp"

static void usage(const char* prog) {
    std::cerr
        << "Federated DAG comparison and profiling tool.\n\n"
        << "Usage:\n"
        << "  " << prog << " <room-slug> [options]\n\n"
        << "Options:\n"
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

    // First positional arg is room
    int i = 1;
    if (argv[1][0] != '-') {
        room = argv[1];
        i = 2;
    }

    for (; i < argc; ++i) {
        if (std::strcmp(argv[i], "--prefix") == 0 && i + 1 < argc) {
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

    if (room.empty()) {
        std::cerr << "Error: room slug is required\n";
        usage(argv[0]);
        return 1;
    }

    if (do_profile) {
        dag::profile(room, prefix, profile_path);
    } else {
        dag::analyze(room, prefix, verbose, rank, chain, version);
    }

    return 0;
}
