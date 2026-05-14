#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>

#include "event.hpp"
#include "jsonl_reader.hpp"

static int failures = 0;

#define ASSERT_EQ(a, b, msg)                                                   \
    do {                                                                       \
        if ((a) != (b)) {                                                      \
            std::cerr << "FAIL: " << (msg) << ": expected " << (b) << ", got " \
                      << (a) << "\n";                                          \
            ++failures;                                                        \
        }                                                                      \
    } while (0)

#define ASSERT_NEAR(a, b, eps, msg)                                 \
    do {                                                            \
        if (std::abs((a) - (b)) > (eps)) {                          \
            std::cerr << "FAIL: " << (msg) << ": expected ~" << (b) \
                      << ", got " << (a) << "\n";                   \
            ++failures;                                             \
        }                                                           \
    } while (0)

#define ASSERT_TRUE(cond, msg)                      \
    do {                                            \
        if (!(cond)) {                              \
            std::cerr << "FAIL: " << (msg) << "\n"; \
            ++failures;                             \
        }                                           \
    } while (0)

static void test_load_event_ids(const std::string& fixture) {
    auto ids = dag::load_event_ids(fixture);
    ASSERT_EQ(ids.size(), 10u, "event count");
    ASSERT_TRUE(ids.count("$create"), "contains $create");
    ASSERT_TRUE(ids.count("$join-alice"), "contains $join-alice");
    ASSERT_TRUE(ids.count("$fork-a"), "contains $fork-a");
    ASSERT_TRUE(ids.count("$leave-bob"), "contains $leave-bob");
    ASSERT_TRUE(!ids.count("$nonexistent"), "no phantom events");
}

static void test_depth_stats(const std::string& fixture) {
    auto stats = dag::get_depth_stats(fixture);
    ASSERT_EQ(stats.min_depth, 1, "min_depth");
    ASSERT_EQ(stats.max_depth, 7, "max_depth");
    ASSERT_EQ(stats.root_event_id, "$create", "root event");

    // Total prev_events:
    //   depth 1: 0, depth 2: 1, depth 3: 1, depth 4: 1+1=2,
    //   depth 5: 2+2+3=7, depth 6: 3, depth 7: 1 → total=15
    // 15 / 10 events = 1.5
    ASSERT_NEAR(stats.branching_factor, 1.5, 0.01, "branching factor");
}

static void test_depth_profile(const std::string& fixture) {
    auto profile = dag::get_depth_profile(fixture);

    // 7 distinct depths
    ASSERT_EQ(profile.size(), 7u, "distinct depths");

    // Depth 1: 1 event, 0 prev
    ASSERT_EQ(profile[1].event_count, 1, "depth 1 events");
    ASSERT_EQ(profile[1].total_prev_events, 0, "depth 1 prev");
    ASSERT_NEAR(profile[1].bf(), 0.0, 0.01, "depth 1 BF");

    // Depth 4: 2 events ($msg1, $msg2), each with 1 prev → 2 total
    ASSERT_EQ(profile[4].event_count, 2, "depth 4 events");
    ASSERT_EQ(profile[4].total_prev_events, 2, "depth 4 prev");
    ASSERT_NEAR(profile[4].bf(), 1.0, 0.01, "depth 4 BF");

    // Depth 5: 3 events, prev counts: 2+2+3=7
    ASSERT_EQ(profile[5].event_count, 3, "depth 5 events (storm)");
    ASSERT_EQ(profile[5].total_prev_events, 7, "depth 5 prev (storm)");
    ASSERT_NEAR(profile[5].bf(), 7.0 / 3.0, 0.01, "depth 5 BF (storm)");

    // Depth 6: 1 event, 3 prev
    ASSERT_EQ(profile[6].event_count, 1, "depth 6 events");
    ASSERT_EQ(profile[6].total_prev_events, 3, "depth 6 prev (merge)");
    ASSERT_NEAR(profile[6].bf(), 3.0, 0.01, "depth 6 BF (merge)");
}

static void test_load_events(const std::string& fixture) {
    auto events = dag::load_events(fixture);
    ASSERT_EQ(events.size(), 10u, "full event count");

    // Verify first event
    ASSERT_EQ(events[0].event_id, "$create", "first event_id");
    ASSERT_EQ(events[0].type, "m.room.create", "first event type");
    ASSERT_EQ(events[0].depth, 1, "first event depth");
    ASSERT_EQ(events[0].room_id, "!test:example.com", "room_id");
    ASSERT_TRUE(events[0].prev_events.empty(), "create has no prev_events");

    // Verify membership extraction
    ASSERT_EQ(events[1].type, "m.room.member", "join-alice type");
    ASSERT_EQ(events[1].membership, "join", "join-alice membership");
    ASSERT_EQ(events[1].state_key, "@alice:example.com",
              "join-alice state_key");

    // Verify leave event
    ASSERT_EQ(events[9].membership, "leave", "leave-bob membership");

    // Verify fork event prev_events count
    ASSERT_EQ(events[7].prev_events.size(), 3u, "triple-parent prev count");
}

static void test_merged_profile(const std::string& fixture) {
    // Merging the same file twice should double event counts
    auto merged = dag::get_merged_depth_profile({fixture, fixture});
    ASSERT_EQ(merged[5].event_count, 6, "merged depth 5 events");
    ASSERT_EQ(merged[5].total_prev_events, 14, "merged depth 5 prev");
    ASSERT_NEAR(merged[5].bf(), 7.0 / 3.0, 0.01, "merged BF unchanged");
}

static constexpr const char* REAL_FILE =
    "/run/media/shane/shane4tb-ent/dags/"
    "remote-dag-c10y-fNiMx5ijtgGFibzPUfNs9hpQvnJYPTV-fD2KPk-v12-"
    "nexy7574.co.uk.jsonl";

static void test_real_file() {
    std::ifstream probe(REAL_FILE);
    if (!probe.is_open()) {
        std::cerr << "SKIP: real file not available\n";
        return;
    }
    probe.close();

    std::cerr << "  real file: load_event_ids...";
    auto ids = dag::load_event_ids(REAL_FILE);
    ASSERT_TRUE(ids.size() > 10000, "real file: has >10k events");
    std::cerr << " " << ids.size() << " events\n";

    std::cerr << "  real file: depth_stats...";
    auto stats = dag::get_depth_stats(REAL_FILE);
    ASSERT_TRUE(stats.min_depth > 0, "real file: min_depth > 0");
    ASSERT_TRUE(stats.max_depth > stats.min_depth, "real file: max > min");
    ASSERT_TRUE(stats.branching_factor > 1.0, "real file: BF > 1.0");
    ASSERT_TRUE(stats.branching_factor < 5.0, "real file: BF < 5.0 (avg)");
    ASSERT_TRUE(!stats.root_event_id.empty(), "real file: has root");
    std::cerr << " depth " << stats.min_depth << ".." << stats.max_depth
              << " BF=" << stats.branching_factor << "\n";

    std::cerr << "  real file: depth_profile...";
    auto profile = dag::get_depth_profile(REAL_FILE);
    ASSERT_TRUE(profile.size() > 1000, "real file: >1000 distinct depths");

    // Verify storm depths exist (BF > 1.5 somewhere)
    bool has_storm = false;
    for (const auto& [d, b] : profile) {
        if (b.bf() > 1.5) {
            has_storm = true;
            break;
        }
    }
    ASSERT_TRUE(has_storm, "real file: has at least one storm depth");
    std::cerr << " " << profile.size() << " depths\n";

    std::cerr << "  real file: load_events...";
    auto events = dag::load_events(REAL_FILE);
    ASSERT_EQ(events.size(), ids.size(), "real file: load_events matches ids");

    // Spot-check: all events have event_id and depth
    for (const auto& ev : events) {
        ASSERT_TRUE(!ev.event_id.empty(), "real file: event has id");
        ASSERT_TRUE(ev.depth > 0, "real file: event has depth > 0");
    }
    std::cerr << " OK\n";
}

int main(int argc, const char* argv[]) {
    std::string fixture = "test/fixtures/storm.jsonl";
    if (argc > 1) {
        fixture = argv[1];
    }

    std::cerr << "=== Fixture tests (" << fixture << ") ===\n";
    test_load_event_ids(fixture);
    test_depth_stats(fixture);
    test_depth_profile(fixture);
    test_load_events(fixture);
    test_merged_profile(fixture);

    std::cerr << "=== Real file test ===\n";
    test_real_file();

    if (failures == 0) {
        std::cerr << "All tests passed.\n";
    } else {
        std::cerr << failures << " test(s) failed.\n";
    }
    return failures > 0 ? 1 : 0;
}
