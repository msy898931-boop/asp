// HOST-0013 - Live decision pipeline implementation.

#include "runtime/DecisionPipeline.h"

namespace aura {

namespace {

std::string makeDecisionId(Timeframe timeframe, std::int64_t barOpenSec,
                           SignalDirection direction) {
    return std::string(toString(timeframe)) + "-" + std::to_string(barOpenSec) +
           "-" + toString(direction);
}

}  // namespace

DecisionPipeline::DecisionPipeline(const IGuardian* guardian,
                                   PredictionLedger* ledger,
                                   PersistenceEngine* persistence,
                                   PositionSimulator* positions,
                                   OutcomeEngine* outcomes,
                                   DecisionPipelineConfig config)
    : config_(config),
      riskEngine_(guardian),
      shadowEngine_(guardian),
      ledger_(ledger),
      persistence_(persistence),
      positions_(positions),
      outcomes_(outcomes) {}

PipelineCycleReport DecisionPipeline::onClosedBar(const Bar& closedBar,
                                                  const std::vector<Bar>& history,
                                                  Timestamp now) {
    PipelineCycleReport report;

    // Causality: only a genuinely closed bar with a valid open time may be
    // evaluated. A negative open time is invalid; epoch 0 is a valid boundary.
    if (closedBar.openTimeSec < 0) {
        report.issues.push_back("closed bar has invalid open time");
        return report;
    }
    if (history.empty()) {
        report.issues.push_back("no closed-bar history available");
        return report;
    }

    const Timeframe timeframe = config_.operationalTimeframe;
    const std::int64_t asOf = closedBar.openTimeSec;

    // Macro/event boundary. A high-impact imminent event suppresses the
    // decision. An absent feed is UNKNOWN (not "clear") and is carried through
    // as an explicit uncertainty rather than fabricated as safe.
    const MacroContext macro = macro_.evaluate(now);
    if (macro.highImpactImminent) {
        report.issues.push_back("macro gate: high-impact event window");
        return report;
    }
    const std::string macroNote =
        macro.valid ? std::string("macro=") + macro.detail : "macro=UNKNOWN";

    const FeatureSnapshot features = featureEngine_.compute(history, timeframe);
    const StructureSnapshot structure =
        structureEngine_.compute(history, features);
    const RegimeState regime = regimeEngine_.compute(features, structure);
    const EligibilityState eligibility =
        eligibilityEngine_.evaluate(features, regime, structure);
    const SignalCandidate candidate =
        signalEngine_.generate(features, structure, regime, eligibility);
    const ScoreResult score =
        scoreEngine_.compute(features, structure, regime, candidate);
    const ConfidenceResult confidence = confidenceEngine_.compute(
        score, structure, regime, candidate, candidate.quality);
    const ProbabilityResult probability =
        probabilityEngine_.compute(score, confidence, candidate);

    if (candidate.direction == SignalDirection::NONE || !score.valid ||
        !confidence.valid) {
        // The chain evaluated this bar; it just found no directional agreement.
        // Record the market read anyway, so the frontend shows the real regime
        // and structure instead of UNKNOWN. "No trade" is a result, not a
        // missing one — and this build claims no edge, so it will often be the
        // honest answer.
        DecisionContext context;
        context.available = true;
        context.symbol = symbol_;
        context.timeframe = timeframe;
        context.asOfBarOpenSec = asOf;
        context.direction = SignalDirection::NONE;
        context.dataState = features.quality;
        context.structure = structure.bias;
        context.regime = regime.regime;
        context.eligibility = eligibility.decision;
        context.strategyVersion = strategyVersion_;
        context.configurationVersion = configurationVersion_;
        context.evaluatedAt = now;
        lastContext_ = context;
        report.issues.push_back("no decision-grade candidate on this bar");
        return report;
    }

    report.decisionProduced = true;

    PipelineDecision decision;
    decision.timeframe = timeframe;
    decision.asOfBarOpenSec = asOf;
    decision.direction = candidate.direction;
    decision.score = score.score;
    decision.confidence = confidence.confidence;
    decision.decisionId =
        EntityId(makeDecisionId(timeframe, asOf, candidate.direction));

    // Capture the actual decision inputs for the frontend contract. These are
    // the values the chain just computed, not re-derived ones.
    DecisionContext context;
    context.available = true;
    context.decisionId = decision.decisionId;
    context.symbol = symbol_;
    context.timeframe = timeframe;
    context.asOfBarOpenSec = asOf;
    context.direction = candidate.direction;
    context.dataState = features.quality;
    context.structure = structure.bias;
    context.regime = regime.regime;
    context.eligibility = eligibility.decision;
    context.strategyVersion = strategyVersion_;
    context.configurationVersion = configurationVersion_;
    context.evaluatedAt = now;
    lastContext_ = context;

    // A decision identity is deterministic; a repeat is a duplicate, never a
    // second prediction.
    if (ledger_ != nullptr && ledger_->contains(decision.decisionId)) {
        decision.reason = "duplicate decision identity; ignored";
        report.decisions.push_back(decision);
        return report;
    }

    const std::string rationale = candidate.rationale + " | " + macroNote;

    PredictionRecord record;
    record.decisionId = decision.decisionId;
    record.timeframe = timeframe;
    record.asOfBarOpenSec = asOf;
    record.direction = candidate.direction;
    record.score = score.score;
    record.confidence = confidence.confidence;
    record.probabilityEstimate = probability.valid ? probability.band.estimate : 0.0;
    record.probabilityCalibrated = false;   // never calibrated in this build
    record.referencePrice = candidate.referencePrice;
    record.stopPrice = candidate.suggestedStop;
    record.targetPrice = candidate.suggestedTarget;
    record.rationale = rationale;
    record.recordedAt = now;

    if (ledger_ != nullptr) {
        decision.recorded = ledger_->append(record);
    }
    if (persistence_ != nullptr) {
        persistence_->persistPrediction(record);
    }

    const MarketQuality marketQuality =
        marketQualityEngine_.evaluate(history, features, features.quality);
    const RiskProposal proposal =
        riskEngine_.propose(candidate, score, marketQuality);

    // The risk proposal is computed (not persisted), so carry it on the
    // decision context for the API to report truthfully.
    lastContext_.risk = proposal;
    lastContext_.riskAvailable = true;

    if (proposal.decision == RiskDecision::DENIED || !proposal.valid) {
        decision.reason = "risk denied: " + proposal.reason;
        report.decisions.push_back(decision);
        return report;
    }

    if (positions_ != nullptr) {
        PortfolioExposure exposure;
        exposure.openPositions = positions_->openCount();
        exposure.aggregateRiskFraction = positions_->aggregateOpenRiskFraction();
        const PortfolioDecision portfolio = portfolioEngine_.admit(exposure, proposal);
        if (!portfolio.allowed) {
            decision.reason = "portfolio denied: " + portfolio.reason;
            report.decisions.push_back(decision);
            return report;
        }

        const ShadowExecutionResult shadow = shadowEngine_.issue(
            proposal, portfolio, decision.decisionId, timeframe, asOf, rationale);
        if (shadow.accepted) {
            decision.shadowIssued = true;
            report.shadowIssued = true;
            positions_->open(shadow.command, asOf, now);
            if (persistence_ != nullptr) {
                const auto& opened = positions_->positions().back();
                persistence_->persistPosition(opened);
            }
        } else {
            decision.reason = "shadow not issued: " + shadow.reason;
        }
    }

    report.decisions.push_back(decision);
    return report;
}

void DecisionPipeline::advancePositions(const Bar& closedBar, Timestamp now,
                                        std::vector<std::string>& issues,
                                        std::vector<EntityId>* outcomesOut) {
    if (positions_ == nullptr) return;
    positions_->advance(closedBar);

    if (outcomes_ == nullptr) return;
    const std::vector<Outcome> newly =
        outcomes_->recordClosed(positions_->positions(), now);
    for (const auto& outcome : newly) {
        if (persistence_ != nullptr && !persistence_->persistOutcome(outcome)) {
            issues.push_back("outcome persistence failed for " +
                             outcome.outcomeId.value());
        }
        if (ledger_ != nullptr) {
            ledger_->linkOutcome(outcome.decisionId, outcome);
        }
        if (outcomesOut != nullptr) {
            outcomesOut->push_back(outcome.outcomeId);
        }
    }
}

}  // namespace aura
