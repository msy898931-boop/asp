// PKG-0009 - Analysis API surface implementation (v1).

#include "api/AnalysisApi.h"

#include "foundation/ServiceState.h"

#include <string>
#include <vector>

namespace aura {
namespace {

constexpr const char* kNull = "null";

const char* regimeLabel(RegimeType regime) noexcept {
    switch (regime) {
        case RegimeType::TRENDING_UP:   return "TREND_UP";
        case RegimeType::TRENDING_DOWN: return "TREND_DOWN";
        case RegimeType::RANGING:       return "RANGE";
        case RegimeType::VOLATILE:      return "VOLATILE";
        case RegimeType::QUIET:         return "QUIET";
        case RegimeType::UNKNOWN:       return "UNKNOWN";
    }
    return "UNKNOWN";
}

const char* h4BiasLabel(StructureBias bias) noexcept {
    switch (bias) {
        case StructureBias::BULLISH: return "UP";
        case StructureBias::BEARISH: return "DOWN";
        case StructureBias::RANGE:   return "RANGE";
        case StructureBias::UNKNOWN: return "UNKNOWN";
    }
    return "UNKNOWN";
}

const char* triggerLabel(SignalDirection direction) noexcept {
    switch (direction) {
        case SignalDirection::LONG:  return "LONG";
        case SignalDirection::SHORT: return "SHORT";
        case SignalDirection::NONE:  return "NONE";
    }
    return "NONE";
}

const char* volatilityLabel(RegimeType regime) noexcept {
    switch (regime) {
        case RegimeType::VOLATILE: return "HIGH";
        case RegimeType::QUIET:    return "LOW";
        case RegimeType::UNKNOWN:  return "UNKNOWN";
        default:                   return "NORMAL";
    }
}

// The `context` object. Fields the backend cannot source are null/UNKNOWN.
std::string contextJson(const std::string& symbol,
                        const DecisionContext& context) {
    std::vector<ApiField> fields;
    if (context.available) {
        fields.push_back({"regime", regimeLabel(context.regime)});
        fields.push_back({"h4_bias", h4BiasLabel(context.structure)});
        fields.push_back({"m15_trigger", triggerLabel(context.direction)});
        // Multi-TF agreement is not computed anywhere yet: honest null.
        fields.push_back({"mtf_agreement", kNull, true});
        fields.push_back({"volatility_state", volatilityLabel(context.regime)});
    } else {
        fields.push_back({"regime", "UNKNOWN"});
        fields.push_back({"h4_bias", "UNKNOWN"});
        fields.push_back({"m15_trigger", "UNKNOWN"});
        fields.push_back({"mtf_agreement", kNull, true});
        fields.push_back({"volatility_state", "UNKNOWN"});
    }
    (void)symbol;
    return jsonObject(fields);
}

// The `signal` object, gated by RULE C. `hasRecord` distinguishes "no
// prediction yet" from "prediction but uncalibrated".
std::string signalJson(const ProbabilityApi::Gate* gate) {
    std::vector<ApiField> fields;
    fields.push_back({"direction", gate != nullptr ? gate->direction : "NONE"});
    // The horizon is a T15 decision that is not frozen yet: honest null.
    fields.push_back({"horizon", kNull, true});
    if (gate != nullptr && gate->presentable) {
        fields.push_back({"probability", jsonNumber(gate->probability), true});
        fields.push_back({"probability_calibrated", jsonBool(true), true});
    } else {
        fields.push_back({"probability", kNull, true});
        fields.push_back({"probability_calibrated", jsonBool(false), true});
    }
    // The uncalibrated score is exposed additively so the frontend can render a
    // "score" without ever misreading it as a probability (RULE C).
    if (gate != nullptr) {
        fields.push_back({"score", jsonNumber(gate->score), true});
    } else {
        fields.push_back({"score", kNull, true});
    }
    // Interval and model version are not sourced yet.
    fields.push_back({"confidence_lo", kNull, true});
    fields.push_back({"confidence_hi", kNull, true});
    fields.push_back({"model_version", kNull, true});
    fields.push_back({"features_contributing", "[]", true});
    return jsonObject(fields);
}

// The `levels` object, sourced from the live risk proposal (never recomputed).
std::string levelsJson(const DecisionContext& context) {
    std::vector<ApiField> fields;
    const bool haveRisk = context.available && context.riskAvailable &&
                          context.risk.valid;
    if (haveRisk) {
        fields.push_back({"entry", jsonNumber(context.risk.entryPrice), true});
        fields.push_back({"stop_loss", jsonNumber(context.risk.stopPrice), true});
        fields.push_back({"take_profit", jsonNumber(context.risk.targetPrice), true});
        std::vector<std::string> ladder;
        ladder.reserve(TARGET_LADDER_SIZE);
        for (const double level : context.risk.targetLadder) {
            ladder.push_back(jsonNumber(level));
        }
        fields.push_back({"targets", jsonArray(ladder), true});
        fields.push_back({"reward_risk",
                          jsonNumber(context.risk.rewardRiskRatio), true});
        fields.push_back({"suggested_risk_pct",
                          jsonNumber(context.risk.riskFraction * 100.0), true});
    } else {
        fields.push_back({"entry", kNull, true});
        fields.push_back({"stop_loss", kNull, true});
        fields.push_back({"take_profit", kNull, true});
        fields.push_back({"targets", jsonArray({}), true});
        fields.push_back({"reward_risk", kNull, true});
        fields.push_back({"suggested_risk_pct", kNull, true});
    }
    // The SL/TP method identifiers are T15 decisions, not yet frozen.
    fields.push_back({"sl_method", kNull, true});
    fields.push_back({"tp_method", kNull, true});
    return jsonObject(fields);
}

// The `meta` object.
std::string metaJson(const ProbabilityApi::Gate* gate, bool degraded) {
    std::vector<ApiField> fields;
    const char* tier = (gate != nullptr && gate->presentable) ? gate->tier
                                                              : "unknown";
    fields.push_back({"coverage_tier", tier});
    // Freshness is owned by the bridge/health layer, not this surface yet.
    fields.push_back({"data_freshness_sec", kNull, true});
    fields.push_back({"degraded", jsonBool(degraded), true});
    fields.push_back({"score_is_probability",
                      jsonBool(gate != nullptr && gate->presentable), true});
    fields.push_back({"disclaimer",
                      "Decision support only. Not financial advice."});
    return jsonObject(fields);
}

}  // namespace

ApiResponse AnalysisApi::latest(const std::string& symbol,
                                const DecisionContext& context,
                                const ProbabilityApi* probability,
                                const PredictionLedger* ledger) const {
    if (probability == nullptr || ledger == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: analysis surface");
    }
    const ProbabilityApi::View view = probability->view(ledger);
    const ProbabilityApi::Gate* gate = view.available ? &view.gate : nullptr;

    std::vector<ApiField> fields;
    if (context.available) {
        fields.push_back({"timestamp", isoUtcSeconds(context.asOfBarOpenSec)});
    } else {
        fields.push_back({"timestamp", kNull, true});
    }
    fields.push_back({"symbol", symbol});
    fields.push_back({"context", contextJson(symbol, context), true});
    fields.push_back({"signal", signalJson(gate), true});
    fields.push_back({"levels", levelsJson(context), true});
    fields.push_back({"meta", metaJson(gate, false), true});
    return ApiResponse{200, "application/json", envelope(jsonObject(fields)), true};
}

ApiResponse AnalysisApi::history(const std::string& symbol,
                                 const DecisionContext& context,
                                 const ProbabilityApi* probability,
                                 const PredictionLedger* ledger,
                                 int limit) const {
    if (probability == nullptr || ledger == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: analysis surface");
    }
    if (limit < 0) limit = 0;
    const std::vector<PredictionRecord>& records = ledger->records();
    std::vector<std::string> elements;
    // Most-recent-first, capped at `limit`.
    for (auto it = records.rbegin();
         it != records.rend() && static_cast<int>(elements.size()) < limit; ++it) {
        const ProbabilityApi::Gate gate = probability->evaluate(*it);
        std::vector<ApiField> fields;
        fields.push_back({"timestamp", isoUtcSeconds(it->asOfBarOpenSec)});
        fields.push_back({"symbol", symbol});
        // Per-entry context is not persisted; report UNKNOWN rather than guess.
        std::vector<ApiField> contextFields;
        contextFields.push_back({"regime", "UNKNOWN"});
        contextFields.push_back({"h4_bias", "UNKNOWN"});
        contextFields.push_back({"m15_trigger", "UNKNOWN"});
        contextFields.push_back({"mtf_agreement", kNull, true});
        contextFields.push_back({"volatility_state", "UNKNOWN"});
        fields.push_back({"context", jsonObject(contextFields), true});
        fields.push_back({"signal", signalJson(&gate), true});
        // Levels are not persisted; report them absent.
        std::vector<ApiField> levelFields;
        levelFields.push_back({"entry", kNull, true});
        levelFields.push_back({"stop_loss", kNull, true});
        levelFields.push_back({"take_profit", kNull, true});
        levelFields.push_back({"reward_risk", kNull, true});
        levelFields.push_back({"suggested_risk_pct", kNull, true});
        levelFields.push_back({"sl_method", kNull, true});
        levelFields.push_back({"tp_method", kNull, true});
        fields.push_back({"levels", jsonObject(levelFields), true});
        fields.push_back({"meta", metaJson(&gate, false), true});
        elements.push_back(jsonObject(fields));
    }
    (void)context;
    return ApiResponse{200, "application/json", envelope(jsonArray(elements)),
                       true};
}

ApiResponse AnalysisApi::contextLatest(const std::string& symbol,
                                       const DecisionContext& context) const {
    std::vector<ApiField> fields;
    fields.push_back({"timestamp", isoUtcSeconds(context.asOfBarOpenSec)});
    fields.push_back({"symbol", symbol});
    fields.push_back({"context", contextJson(symbol, context), true});
    return ApiResponse{200, "application/json", envelope(jsonObject(fields)), true};
}

ApiResponse AnalysisApi::health(const HealthMonitor* health,
                                const std::string& bridgeState,
                                bool bridgeHandshakeOk,
                                std::int64_t uptimeSec) const {
    if (health == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: health monitor");
    }
    const BackendHealth snapshot = health->snapshot();

    // Map the service aggregate onto the frontend's ok/degraded/offline view.
    const char* status = "degraded";
    switch (snapshot.aggregate) {
        case ServiceState::ONLINE:   status = "ok";       break;
        case ServiceState::OFFLINE:
        case ServiceState::ERROR:
        case ServiceState::BLOCKED:  status = "offline";  break;
        default:                     status = "degraded"; break;
    }

    const char* bridge = "offline";
    if (bridgeState == "ONLINE") {
        bridge = bridgeHandshakeOk ? "ok" : "degraded";
    } else if (bridgeState == "STARTING" || bridgeState == "RECOVERING") {
        bridge = "degraded";
    }

    std::vector<ApiField> fields;
    fields.push_back({"status", status});
    fields.push_back({"bridge", bridge});
    fields.push_back({"version", kApiVersion});
    if (uptimeSec >= 0) {
        fields.push_back({"uptime_sec", jsonInteger(uptimeSec), true});
    } else {
        fields.push_back({"uptime_sec", kNull, true});
    }
    return ApiResponse{200, "application/json", envelope(jsonObject(fields)), true};
}

}  // namespace aura
