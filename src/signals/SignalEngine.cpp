// DEC-0013 - Signal engine implementation.

#include "signals/SignalEngine.h"

namespace aura {

SignalCandidate SignalEngine::generate(const FeatureSnapshot& features,
                                       const StructureSnapshot& structure,
                                       const RegimeState& regime,
                                       const EligibilityState& eligibility) const {
    SignalCandidate candidate;
    candidate.timeframe = features.timeframe;
    candidate.asOfBarOpenSec = features.asOfBarOpenSec;
    candidate.referencePrice = features.lastClose;

    if (!features.valid) {
        candidate.quality = features.quality;
        candidate.rationale = "features unavailable";
        return candidate;
    }
    if (eligibility.decision != EligibilityDecision::ELIGIBLE) {
        candidate.quality = DataQualityState::VALID;
        candidate.valid = true;
        candidate.direction = SignalDirection::NONE;
        candidate.rationale = std::string("not eligible: ") + toString(eligibility.decision);
        return candidate;
    }

    // Directional vote: structure bias + regime trend + RSI momentum.
    int longVotes = 0;
    int shortVotes = 0;
    if (structure.bias == StructureBias::BULLISH) ++longVotes;
    if (structure.bias == StructureBias::BEARISH) ++shortVotes;
    if (regime.regime == RegimeType::TRENDING_UP) ++longVotes;
    if (regime.regime == RegimeType::TRENDING_DOWN) ++shortVotes;
    if (features.rsi >= config_.rsiLongThreshold) ++longVotes;
    if (features.rsi <= config_.rsiShortThreshold) ++shortVotes;

    if (longVotes > shortVotes && longVotes >= 2) {
        candidate.direction = SignalDirection::LONG;
        candidate.rawStrength = static_cast<double>(longVotes) / 3.0;
    } else if (shortVotes > longVotes && shortVotes >= 2) {
        candidate.direction = SignalDirection::SHORT;
        candidate.rawStrength = static_cast<double>(shortVotes) / 3.0;
    } else {
        candidate.direction = SignalDirection::NONE;
        candidate.rawStrength = 0.0;
    }

    if (candidate.direction == SignalDirection::LONG) {
        candidate.suggestedStop =
            features.lastClose - config_.atrStopMultiplier * features.atr;
        candidate.suggestedTarget =
            features.lastClose + config_.atrTargetMultiplier * features.atr;
    } else if (candidate.direction == SignalDirection::SHORT) {
        candidate.suggestedStop =
            features.lastClose + config_.atrStopMultiplier * features.atr;
        candidate.suggestedTarget =
            features.lastClose - config_.atrTargetMultiplier * features.atr;
    }

    // The scaled ladder: each rung a fraction of the target distance, nearest
    // first. The last rung equals suggestedTarget exactly, so a reader of the
    // ladder and a reader of the single target see the same final level.
    if (candidate.direction != SignalDirection::NONE) {
        const double sign = (candidate.direction == SignalDirection::LONG) ? 1.0 : -1.0;
        const double distance =
            config_.atrTargetMultiplier * features.atr;
        for (std::size_t i = 0; i < TARGET_LADDER_SIZE; ++i) {
            candidate.suggestedTargets[i] =
                features.lastClose + sign * config_.targetLadderFractions[i] * distance;
        }
    }

    candidate.rationale = "structure=" + std::string(toString(structure.bias)) +
                          " regime=" + std::string(toString(regime.regime)) +
                          " longVotes=" + std::to_string(longVotes) +
                          " shortVotes=" + std::to_string(shortVotes);
    candidate.valid = true;
    candidate.quality = DataQualityState::VALID;
    return candidate;
}

}  // namespace aura
