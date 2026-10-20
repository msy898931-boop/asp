#pragma once
// DEC-0024 - Deterministic risk engine.
//
// Converts a scored signal into a bounded risk proposal. The engine may only
// reduce risk: it never exceeds the Guardian's per-decision risk fraction and
// it denies proposals when the Guardian, market quality, or score forbids them.

#include "guardian/IGuardian.h"
#include "market_quality/MarketQualityEngine.h"
#include "scoring/ScoreEngine.h"
#include "signals/SignalEngine.h"

#include <string>
#include <vector>

namespace aura {

enum class RiskDecision {
    APPROVED,
    REDUCED,
    DENIED,
};

inline const char* toString(RiskDecision d) noexcept {
    switch (d) {
        case RiskDecision::APPROVED: return "APPROVED";
        case RiskDecision::REDUCED:  return "REDUCED";
        case RiskDecision::DENIED:   return "DENIED";
    }
    return "DENIED";
}

struct RiskProposal {
    SignalDirection direction = SignalDirection::NONE;
    double entryPrice = 0.0;
    double stopPrice = 0.0;
    double targetPrice = 0.0;
    // The scaled ladder from the signal candidate, nearest first. targetPrice
    // is the furthest rung; the rungs below it are the partial exits.
    std::array<double, TARGET_LADDER_SIZE> targetLadder{};
    double riskFraction = 0.0;    // fraction of equity at risk
    double riskAmount = 0.0;
    double positionSizeLots = 0.0;
    double rewardRiskRatio = 0.0;
    RiskDecision decision = RiskDecision::DENIED;
    std::string reason;
    bool valid = false;
};

struct RiskConfig {
    double accountEquity = 10000.0;
    double contractSize = 100.0;      // XAUUSD: 100 oz per lot
    double minScore = 40.0;
    double minConfidence = 0.2;
    double minMarketQuality = 0.4;
    double pointValue = 1.0;          // value per point per lot
};

class RiskEngine {
public:
    RiskEngine(const IGuardian* guardian, RiskConfig config = {})
        : guardian_(guardian), config_(config) {}

    RiskProposal propose(const SignalCandidate& candidate,
                         const ScoreResult& score,
                         const MarketQuality& marketQuality) const;

    const RiskConfig& config() const noexcept { return config_; }

private:
    const IGuardian* guardian_;
    RiskConfig config_;
};

}  // namespace aura
