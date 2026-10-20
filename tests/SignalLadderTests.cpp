// TST-LADDER - the scaled-target ladder on the signal and risk proposals.
//
// The last rung must equal the single target exactly, so the shadow simulator,
// the ledger, and every older reader of suggestedTarget/targetPrice stay
// unchanged while the nearer rungs carry the partial exits.

#include "TestHarness.h"

#include "eligibility/EligibilityEngine.h"
#include "features/FeatureEngine.h"
#include "guardian/IGuardian.h"
#include "market_quality/MarketQualityEngine.h"
#include "regime/RegimeEngine.h"
#include "risk/RiskEngine.h"
#include "scoring/ScoreEngine.h"
#include "signals/SignalEngine.h"
#include "structure/StructureEngine.h"

#include <algorithm>
#include <cmath>
#include <memory>
#include <vector>

using namespace aura;

namespace {

constexpr std::int64_t kM15 = 15 * 60;

std::vector<Bar> trendingBars(std::size_t count, double start, double slope) {
    std::vector<Bar> bars;
    for (std::size_t i = 0; i < count; ++i) {
        const double base = start + slope * static_cast<double>(i);
        const double wobble = std::sin(static_cast<double>(i) * 0.7) * 0.8;
        const double open = base + wobble;
        const double close = base + slope * 0.6;
        const double high = std::max(open, close) + 0.6;
        const double low = std::min(open, close) - 0.6;
        bars.push_back(test::makeBar(Timeframe::M15,
                                     static_cast<std::int64_t>(i) * kM15, open,
                                     high, low, close));
    }
    return bars;
}

SignalCandidate candidateFor(const std::vector<Bar>& bars) {
    const FeatureEngine featureEngine;
    const StructureEngine structureEngine;
    const RegimeEngine regimeEngine;
    const EligibilityEngine eligibilityEngine;
    const SignalEngine signalEngine;

    const FeatureSnapshot features = featureEngine.compute(bars, Timeframe::M15);
    const StructureSnapshot structure = structureEngine.compute(bars, features);
    const RegimeState regime = regimeEngine.compute(features, structure);
    const EligibilityState eligibility =
        eligibilityEngine.evaluate(features, regime, structure);
    return signalEngine.generate(features, structure, regime, eligibility);
}

ScoreResult validScore() {
    ScoreResult score;
    score.score = 75.0;
    score.quality = DataQualityState::VALID;
    score.valid = true;
    return score;
}

MarketQuality validMarketQuality() {
    MarketQuality quality;
    quality.timeframe = Timeframe::M15;
    quality.overall = 0.8;
    quality.quality = DataQualityState::VALID;
    quality.valid = true;
    return quality;
}

}  // namespace

TEST_CASE(signal_ladder_fills_five_ascending_rungs_on_a_long) {
    const SignalCandidate candidate = candidateFor(trendingBars(120, 2000.0, 0.35));
    CHECK(candidate.direction == SignalDirection::LONG);

    for (std::size_t i = 1; i < TARGET_LADDER_SIZE; ++i) {
        CHECK(candidate.suggestedTargets[i] > candidate.suggestedTargets[i - 1]);
    }
    for (std::size_t i = 0; i < TARGET_LADDER_SIZE; ++i) {
        CHECK(candidate.suggestedTargets[i] > candidate.referencePrice);
    }
}

TEST_CASE(signal_ladder_last_rung_equals_the_single_target) {
    const SignalCandidate candidate = candidateFor(trendingBars(120, 2000.0, 0.35));
    CHECK(candidate.direction != SignalDirection::NONE);
    CHECK(std::abs(candidate.suggestedTargets[TARGET_LADDER_SIZE - 1] -
                   candidate.suggestedTarget) < 1e-9);
}

TEST_CASE(signal_ladder_descends_on_a_short) {
    const SignalCandidate candidate =
        candidateFor(trendingBars(120, 2000.0, -0.35));
    CHECK(candidate.direction == SignalDirection::SHORT);

    for (std::size_t i = 1; i < TARGET_LADDER_SIZE; ++i) {
        CHECK(candidate.suggestedTargets[i] < candidate.suggestedTargets[i - 1]);
    }
    for (std::size_t i = 0; i < TARGET_LADDER_SIZE; ++i) {
        CHECK(candidate.suggestedTargets[i] < candidate.referencePrice);
    }
    CHECK(std::abs(candidate.suggestedTargets[TARGET_LADDER_SIZE - 1] -
                   candidate.suggestedTarget) < 1e-9);
}

TEST_CASE(risk_proposal_carries_the_signal_ladder) {
    const SignalCandidate candidate = candidateFor(trendingBars(120, 2000.0, 0.35));
    CHECK(candidate.direction != SignalDirection::NONE);

    std::unique_ptr<IGuardian> guardian = makeGuardian();
    const RiskEngine riskEngine(guardian.get());
    const RiskProposal proposal =
        riskEngine.propose(candidate, validScore(), validMarketQuality());

    CHECK_EQ(proposal.targetPrice, candidate.suggestedTarget);
    for (std::size_t i = 0; i < TARGET_LADDER_SIZE; ++i) {
        CHECK_EQ(proposal.targetLadder[i], candidate.suggestedTargets[i]);
    }
}
