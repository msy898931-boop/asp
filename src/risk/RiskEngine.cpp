// DEC-0025 - Risk engine implementation.

#include "risk/RiskEngine.h"

#include <algorithm>
#include <cmath>

namespace aura {

RiskProposal RiskEngine::propose(const SignalCandidate& candidate,
                                 const ScoreResult& score,
                                 const MarketQuality& marketQuality) const {
    RiskProposal proposal;
    proposal.direction = candidate.direction;
    proposal.entryPrice = candidate.referencePrice;
    proposal.stopPrice = candidate.suggestedStop;
    proposal.targetPrice = candidate.suggestedTarget;
    proposal.targetLadder = candidate.suggestedTargets;

    if (candidate.direction == SignalDirection::NONE) {
        proposal.decision = RiskDecision::DENIED;
        proposal.reason = "no directional candidate";
        return proposal;
    }
    if (!score.valid || !marketQuality.valid) {
        proposal.decision = RiskDecision::DENIED;
        proposal.reason = "score or market quality unavailable";
        return proposal;
    }
    if (score.score < config_.minScore) {
        proposal.decision = RiskDecision::DENIED;
        proposal.reason = "score below minimum threshold";
        return proposal;
    }
    if (marketQuality.overall < config_.minMarketQuality) {
        proposal.decision = RiskDecision::DENIED;
        proposal.reason = "market quality below minimum threshold";
        return proposal;
    }

    // Guardian is the authority boundary. A shadow execution request is
    // evaluated explicitly; denial is honoured without exception.
    double maxRiskFraction = config_.accountEquity > 0.0 ? 0.01 : 0.0;
    if (guardian_ != nullptr) {
        GuardianRequest request;
        request.action = "shadow.execute";
        request.actor = "risk-engine";
        request.affectsExecution = true;
        request.isLiveExecution = false;
        const GuardianVerdict verdict = guardian_->evaluate(request);
        if (verdict.decision == GuardianDecision::DENY) {
            proposal.decision = RiskDecision::DENIED;
            proposal.reason = "guardian denied: " + verdict.reason;
            return proposal;
        }
        maxRiskFraction = guardian_->policy().maxRiskFractionPerDecision;
    }

    // Risk per trade is scaled down by score and market quality; it can never
    // exceed the Guardian's cap.
    double riskFraction = maxRiskFraction;
    riskFraction *= std::max(0.25, score.score / 100.0);
    riskFraction *= std::max(0.25, marketQuality.overall);
    riskFraction = std::min(riskFraction, maxRiskFraction);

    const double stopDistance = std::fabs(proposal.entryPrice - proposal.stopPrice);
    if (stopDistance <= 0.0) {
        proposal.decision = RiskDecision::DENIED;
        proposal.reason = "zero stop distance";
        return proposal;
    }

    proposal.riskFraction = riskFraction;
    proposal.riskAmount = riskFraction * config_.accountEquity;

    // Position sizing: risk amount / (stop distance * value per point * contract)
    const double valuePerPriceUnit = config_.pointValue * config_.contractSize;
    const double riskPerLot = stopDistance * valuePerPriceUnit;
    if (riskPerLot <= 0.0) {
        proposal.decision = RiskDecision::DENIED;
        proposal.reason = "invalid contract specification";
        return proposal;
    }
    proposal.positionSizeLots = proposal.riskAmount / riskPerLot;

    const double rewardDistance = std::fabs(proposal.targetPrice - proposal.entryPrice);
    proposal.rewardRiskRatio =
        stopDistance > 0.0 ? rewardDistance / stopDistance : 0.0;

    proposal.decision = riskFraction < maxRiskFraction ? RiskDecision::REDUCED
                                                       : RiskDecision::APPROVED;
    proposal.reason = "risk bounded by guardian policy";
    proposal.valid = true;
    return proposal;
}

}  // namespace aura
