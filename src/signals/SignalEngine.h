#pragma once
// DEC-0012 - Deterministic signal engine.
//
// Produces a directional candidate from features, structure, and regime, but
// only when eligibility permits. A NOT_ELIGIBLE state yields NO_SIGNAL; it
// never yields a fabricated direction.

#include "eligibility/EligibilityEngine.h"
#include "features/FeatureSnapshot.h"
#include "foundation/DataQualityState.h"
#include "regime/RegimeState.h"
#include "structure/StructureSnapshot.h"

#include <array>
#include <cstdint>
#include <string>

namespace aura {

// Number of rungs in the scaled-target ladder. Matches the advisor's Python
// ladder (advisor/mtf_read.py) so the phone app and the web terminal show the
// same shape.
constexpr std::size_t TARGET_LADDER_SIZE = 5;

enum class SignalDirection {
    LONG,
    SHORT,
    NONE,
};

inline const char* toString(SignalDirection d) noexcept {
    switch (d) {
        case SignalDirection::LONG:  return "LONG";
        case SignalDirection::SHORT: return "SHORT";
        case SignalDirection::NONE:  return "NONE";
    }
    return "NONE";
}

struct SignalCandidate {
    Timeframe timeframe = Timeframe::M15;
    std::int64_t asOfBarOpenSec = 0;
    SignalDirection direction = SignalDirection::NONE;
    double rawStrength = 0.0;     // 0..1 pre-score
    double referencePrice = 0.0;
    double suggestedStop = 0.0;
    double suggestedTarget = 0.0;
    // The scaled ladder: TARGET_LADDER_SIZE levels, nearest first. suggestedTarget
    // is the furthest rung, so the two never disagree.
    std::array<double, TARGET_LADDER_SIZE> suggestedTargets{};
    std::string rationale;
    DataQualityState quality = DataQualityState::UNKNOWN;
    bool valid = false;
};

struct SignalConfig {
    double rsiLongThreshold = 52.0;
    double rsiShortThreshold = 48.0;
    double atrStopMultiplier = 1.5;
    double atrTargetMultiplier = 2.5;
    // The scaled ladder, nearest first, as fractions of the target distance
    // (atrTargetMultiplier * atr). The last rung is 1.0, so suggestedTarget —
    // and therefore the shadow simulator and the ledger — are unchanged; the
    // rungs below it are the partial exits. The shape matches the advisor's
    // Python ladder so the phone app and the terminal show one plan.
    std::array<double, TARGET_LADDER_SIZE> targetLadderFractions =
        {0.3, 0.5, 0.7, 0.875, 1.0};
};

class SignalEngine {
public:
    explicit SignalEngine(SignalConfig config = {}) : config_(config) {}

    SignalCandidate generate(const FeatureSnapshot& features,
                             const StructureSnapshot& structure,
                             const RegimeState& regime,
                             const EligibilityState& eligibility) const;

private:
    SignalConfig config_;
};

}  // namespace aura
