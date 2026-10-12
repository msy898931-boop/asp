// TST-0011 - Live decision pipeline integration (closed bar -> shadow).
//
// Exercises the real decision chain end to end through DecisionPipeline:
// features -> structure -> regime -> eligibility -> signal -> score ->
// confidence -> probability -> macro/market-quality -> risk -> portfolio ->
// shadow execution -> position -> persistence. No mocks: the real engines,
// ledger, simulator, guardian, and file store are used.

#include "TestHarness.h"

#include "guardian/IGuardian.h"
#include "ledger/PredictionLedger.h"
#include "outcomes/OutcomeEngine.h"
#include "persistence/FilePersistenceStore.h"
#include "persistence/PersistenceEngine.h"
#include "runtime/DecisionPipeline.h"
#include "shadow/PositionSimulator.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

using namespace aura;

namespace {

// A wavy uptrend: strong enough to be trending/bullish, oscillating enough to
// form swing pivots. Deterministic, no randomness.
std::vector<Bar> syntheticUptrend(Timeframe timeframe, std::int64_t startOpen,
                                  std::size_t count) {
    std::vector<Bar> bars;
    const double pi = 3.14159265358979323846;
    const std::int64_t step = 60;   // one minute spacing (timeframe-agnostic here)
    for (std::size_t i = 0; i < count; ++i) {
        const double base = 2000.0 + static_cast<double>(i) * 0.3;
        const double wave = 15.0 * std::sin(static_cast<double>(i) * 2.0 * pi / 12.0);
        const double close = base + wave;
        const std::int64_t openSec = startOpen + static_cast<std::int64_t>(i) * step;
        bars.push_back(test::makeBar(timeframe, openSec, close - 0.2, close + 1.0,
                                     close - 1.0, close));
    }
    return bars;
}

// The mirror image: a wavy downtrend, so the short side is exercised with the
// same shape as the long side.
std::vector<Bar> syntheticDowntrend(Timeframe timeframe, std::int64_t startOpen,
                                    std::size_t count) {
    std::vector<Bar> bars = syntheticUptrend(timeframe, startOpen, count);
    // Mirror around a *fixed* level: a moving pivot would keep the drift upward.
    const double pivot = 2000.0;
    for (Bar& bar : bars) {
        bar.open = 2.0 * pivot - bar.open;
        bar.high = 2.0 * pivot - bar.high;
        bar.low = 2.0 * pivot - bar.low;
        bar.close = 2.0 * pivot - bar.close;
        // Mirroring flips high/low; restore their invariant.
        if (bar.low > bar.high) std::swap(bar.low, bar.high);
    }
    return bars;
}

}  // namespace

TEST_CASE(closed_bar_produces_decision_and_shadow_command) {
    const std::string root = "/tmp/aura-pipeline-test";
    std::filesystem::remove_all(root);

    auto guardian = makeGuardian();
    PredictionLedger ledger;
    PositionSimulator positions;
    OutcomeEngine outcomes;
    FilePersistenceStore store(root);
    REQUIRE(store.isAvailable());
    PersistenceEngine persistence(&store);

    DecisionPipeline pipeline(guardian.get(), &ledger, &persistence, &positions,
                              &outcomes);
    const std::vector<Bar> bars =
        syntheticUptrend(Timeframe::M15, 1700000000, 200);
    REQUIRE(bars.size() >= 60);

    const Timestamp now = Timestamp::fromEpochMillis(
        (bars.back().openTimeSec + 900) * 1000);

    const PipelineCycleReport report =
        pipeline.onClosedBar(bars.back(), bars, now);

    CHECK(report.decisionProduced);
    REQUIRE(report.decisions.size() == 1);
    const PipelineDecision& decision = report.decisions[0];
    CHECK_EQ(static_cast<int>(decision.direction),
             static_cast<int>(SignalDirection::LONG));
    CHECK(decision.score > 0.0);
    CHECK(decision.confidence > 0.0);
    CHECK(decision.recorded);
    CHECK(decision.shadowIssued);
    CHECK_EQ(ledger.size(), static_cast<std::size_t>(1));
    CHECK_EQ(positions.openCount(), static_cast<std::size_t>(1));

    // Probability must remain explicitly uncalibrated and score must never be
    // recorded as a probability.
    const PredictionRecord& record = ledger.records().back();
    CHECK(!record.probabilityCalibrated);

    // The prediction must have been persisted to the durable store.
    std::string payload;
    PersistenceRecordMetadata metadata;
    CHECK_EQ(static_cast<int>(store.get("predictions", record.decisionId.value(),
                                        payload, metadata)),
             static_cast<int>(PersistenceStatus::OK));

    // The shadow command carries the deterministic decision identity.
    CHECK_EQ(decision.decisionId.value(),
             std::string("M15-") + std::to_string(decision.asOfBarOpenSec) +
                 "-LONG");

    std::filesystem::remove_all(root);
}

TEST_CASE(re_evaluating_the_same_closed_bar_is_a_no_op) {
    const std::string root = "/tmp/aura-pipeline-dup";
    std::filesystem::remove_all(root);

    auto guardian = makeGuardian();
    PredictionLedger ledger;
    PositionSimulator positions;
    OutcomeEngine outcomes;
    FilePersistenceStore store(root);
    PersistenceEngine persistence(&store);

    DecisionPipeline pipeline(guardian.get(), &ledger, &persistence, &positions,
                              &outcomes);
    const std::vector<Bar> bars =
        syntheticUptrend(Timeframe::M15, 1700000000, 200);
    const Timestamp now = Timestamp::fromEpochMillis(
        (bars.back().openTimeSec + 900) * 1000);

    const PipelineCycleReport first =
        pipeline.onClosedBar(bars.back(), bars, now);
    REQUIRE(first.shadowIssued);
    const std::size_t ledgerAfterFirst = ledger.size();
    const std::size_t positionsAfterFirst = positions.openCount();

    // Same bar again: the decision identity is deterministic, so it is a
    // duplicate and must not create a second prediction or position.
    const PipelineCycleReport second =
        pipeline.onClosedBar(bars.back(), bars, now);
    CHECK_EQ(ledger.size(), ledgerAfterFirst);
    CHECK_EQ(positions.openCount(), positionsAfterFirst);
    CHECK(!second.shadowIssued);

    std::filesystem::remove_all(root);
}

TEST_CASE(downtrend_produces_a_short_decision_and_shadow_command) {
    const std::string root = "/tmp/aura-pipeline-short-dir";
    std::filesystem::remove_all(root);

    auto guardian = makeGuardian();
    PredictionLedger ledger;
    PositionSimulator positions;
    OutcomeEngine outcomes;
    FilePersistenceStore store(root);
    REQUIRE(store.isAvailable());
    PersistenceEngine persistence(&store);

    DecisionPipeline pipeline(guardian.get(), &ledger, &persistence, &positions,
                              &outcomes);
    const std::vector<Bar> bars =
        syntheticDowntrend(Timeframe::M15, 1700000000, 200);
    REQUIRE(bars.size() >= 60);

    const Timestamp now = Timestamp::fromEpochMillis(
        (bars.back().openTimeSec + 900) * 1000);

    const PipelineCycleReport report =
        pipeline.onClosedBar(bars.back(), bars, now);

    CHECK(report.decisionProduced);
    REQUIRE(report.decisions.size() == 1);
    const PipelineDecision& decision = report.decisions[0];
    // The engine is symmetric: the same chain that yields LONG on an uptrend
    // must yield SHORT on the mirror image, with the stop above and the target
    // below the entry.
    CHECK_EQ(static_cast<int>(decision.direction),
             static_cast<int>(SignalDirection::SHORT));
    CHECK(decision.shadowIssued);
    CHECK_EQ(ledger.size(), static_cast<std::size_t>(1));
    CHECK_EQ(positions.openCount(), static_cast<std::size_t>(1));

    const PredictionRecord& record = ledger.records().back();
    CHECK(record.stopPrice > record.referencePrice);
    CHECK(record.targetPrice < record.referencePrice);
    CHECK_EQ(record.decisionId.value(),
             std::string("M15-") + std::to_string(record.asOfBarOpenSec) +
                 "-SHORT");

    std::filesystem::remove_all(root);
}

TEST_CASE(insufficient_history_yields_no_decision) {
    const std::string root = "/tmp/aura-pipeline-short";
    std::filesystem::remove_all(root);

    auto guardian = makeGuardian();
    PredictionLedger ledger;
    PositionSimulator positions;
    OutcomeEngine outcomes;
    FilePersistenceStore store(root);
    PersistenceEngine persistence(&store);

    DecisionPipeline pipeline(guardian.get(), &ledger, &persistence, &positions,
                              &outcomes);
    const std::vector<Bar> bars = syntheticUptrend(Timeframe::M15, 1700000000, 10);
    const Timestamp now = Timestamp::fromEpochMillis(
        (bars.back().openTimeSec + 900) * 1000);

    const PipelineCycleReport report =
        pipeline.onClosedBar(bars.back(), bars, now);
    CHECK(!report.decisionProduced);
    CHECK_EQ(ledger.size(), static_cast<std::size_t>(0));
    CHECK_EQ(positions.openCount(), static_cast<std::size_t>(0));

    std::filesystem::remove_all(root);
}
