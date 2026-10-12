// PKG-0002 - Backend facade implementation.

#include "api/BackendFacade.h"

#include "resilience/TimeframeCapabilityImpact.h"

#include <sstream>

namespace aura {

namespace {

ApiResponse unavailable(const std::string& what) {
    return errorResponse(503, "dependency_unavailable",
                         std::string("backend component unavailable: ") + what);
}

std::string qualityJson(DataQualityState quality) {
    std::vector<ApiField> fields;
    fields.push_back({"state", toString(quality)});
    fields.push_back({"decision_grade", isDecisionGrade(quality) ? "true" : "false",
                      true});
    return jsonObject(fields);
}

std::string freshnessJson(const FreshnessInfo& freshness) {
    std::vector<ApiField> fields;
    fields.push_back({"state", toString(freshness.state)});
    fields.push_back({"is_fresh", jsonBool(freshness.isFresh()), true});
    fields.push_back({"last_update",
                      jsonInteger(freshness.lastUpdate.epochMillis()), true});
    fields.push_back({"age_millis", jsonInteger(freshness.ageMillis), true});
    fields.push_back({"max_age_millis", jsonInteger(freshness.maxAgeMillis), true});
    return jsonObject(fields);
}

std::string capabilityImpactJson(Timeframe timeframe, DataQualityState quality) {
    std::vector<std::string> elements;
    for (const auto& impact : timeframeCapabilityImpact(timeframe, quality)) {
        std::vector<ApiField> fields;
        fields.push_back({"capability", impact.capability});
        fields.push_back({"impact", impact.impact});
        fields.push_back({"reason", impact.reason});
        elements.push_back(jsonObject(fields));
    }
    return jsonArray(elements);
}

}  // namespace

ApiResponse BackendFacade::systemState() const {
    if (deps_.runtime == nullptr) return unavailable("runtime");
    const SystemMode mode = deps_.runtime->mode();
    std::vector<ApiField> fields;
    fields.push_back({"mode", toString(mode)});
    fields.push_back({"shadow_only", jsonBool(true), true});
    fields.push_back({"ready", jsonBool(deps_.runtime->isReady()), true});
    fields.push_back({"bridge_state", toString(deps_.runtime->bridgeState())});
    fields.push_back({"startup_stage", toString(deps_.runtime->startupStage())});
    fields.push_back({"api", kApiVersion});
    fields.push_back({"schema", kApiSchemaVersion.toString()});
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::health() const {
    if (deps_.health == nullptr) return unavailable("health monitor");
    const BackendHealth snapshot = deps_.health->snapshot();

    std::vector<std::string> reasons;
    for (const auto& reason : snapshot.degradedReasons) {
        reasons.push_back(jsonString(reason));
    }
    std::vector<ApiField> fields;
    fields.push_back({"aggregate", toString(snapshot.aggregate)});
    fields.push_back({"decision_grade_data", jsonBool(snapshot.decisionGradeData), true});
    fields.push_back({"degraded_reasons", jsonArray(reasons), true});
    fields.push_back({"observed_at", jsonInteger(snapshot.observedAt.epochMillis()), true});
    fields.push_back({"unknown_is_not_safe", jsonBool(true), true});
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::timeframes() const {
    if (deps_.runtime == nullptr) return unavailable("runtime");
    const auto snapshot = deps_.runtime->timeframeStore().snapshot();

    // Iterate the canonical timeframe order, not the map's lexicographic order,
    // so the contract is stable regardless of which streams were observed.
    std::vector<std::string> elements;
    for (Timeframe timeframe : allTimeframes()) {
        const std::string name = toString(timeframe);
        auto it = snapshot.find(name);
        std::vector<ApiField> fields;
        fields.push_back({"timeframe", name});
        if (it == snapshot.end()) {
            // Never observed: explicit unknown, never an empty-but-fine state.
            fields.push_back({"observed", jsonBool(false), true});
            fields.push_back({"has_closed_bar", jsonBool(false), true});
            fields.push_back({"quality", qualityJson(DataQualityState::UNKNOWN), true});
            fields.push_back({"decision_grade", jsonBool(false), true});
            fields.push_back({"freshness", "null", true});
            fields.push_back({"last_successful_update", "null", true});
            fields.push_back({"capability_impact",
                              capabilityImpactJson(timeframe,
                                                   DataQualityState::UNKNOWN),
                              true});
            elements.push_back(jsonObject(fields));
            continue;
        }
        const TimeframeState& state = it->second;
        fields.push_back({"observed", jsonBool(true), true});
        fields.push_back({"has_closed_bar", jsonBool(state.hasClosedBar), true});
        fields.push_back({"quality", qualityJson(state.quality), true});
        fields.push_back({"decision_grade", jsonBool(isDecisionGrade(state.quality)), true});
        fields.push_back({"freshness", freshnessJson(state.freshness), true});
        // Last successful update is the last closed-bar open time when present;
        // otherwise the freshness last-update, which may be unknown.
        if (state.hasClosedBar) {
            fields.push_back({"last_successful_update",
                              jsonInteger(state.lastClosedBar.openTimeSec), true});
        } else if (state.freshness.lastUpdate.isKnown()) {
            fields.push_back({"last_successful_update",
                              jsonInteger(state.freshness.lastUpdate.epochMillis()),
                              true});
        } else {
            fields.push_back({"last_successful_update", "null", true});
        }
        fields.push_back({"last_closed_bar_open",
                          jsonInteger(state.lastClosedBar.openTimeSec), true});
        fields.push_back({"sequence",
                          jsonInteger(static_cast<std::int64_t>(state.sequence)), true});
        fields.push_back({"capability_impact",
                          capabilityImpactJson(timeframe, state.quality), true});
        elements.push_back(jsonObject(fields));
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonArray(elements)), true};
}

ApiResponse BackendFacade::timeframeSnapshot(const std::string& timeframe) const {
    if (deps_.runtime == nullptr) return unavailable("runtime");
    Timeframe parsed;
    bool matched = false;
    for (Timeframe tf : allTimeframes()) {
        if (timeframe == toString(tf)) {
            parsed = tf;
            matched = true;
            break;
        }
    }
    if (!matched) {
        return errorResponse(400, "unknown_timeframe",
                             "unknown timeframe: " + timeframe);
    }

    TimeframeState state;
    if (!deps_.runtime->timeframeStore().get(parsed, state)) {
        // Explicit not-observed state; never an empty-but-fine snapshot.
        // D-1: emit the SAME quality shape as every other path (an object),
        // not a bare string. A branch that disagrees with its own contract is a
        // bug; this route is the canonical timeframe display contract.
        std::vector<ApiField> fields;
        fields.push_back({"timeframe", timeframe});
        fields.push_back({"has_closed_bar", jsonBool(false), true});
        fields.push_back({"quality",
                          qualityJson(DataQualityState::UNKNOWN), true});
        fields.push_back({"observed", jsonBool(false), true});
        fields.push_back({"freshness", "null", true});
        fields.push_back({"last_successful_update", "null", true});
        fields.push_back({"capability_impact",
                          capabilityImpactJson(parsed, DataQualityState::UNKNOWN),
                          true});
        return ApiResponse{200, "application/json",
                           envelope(jsonObject(fields)), true};
    }

    std::vector<ApiField> fields;
    fields.push_back({"timeframe", timeframe});
    fields.push_back({"has_closed_bar", jsonBool(state.hasClosedBar), true});
    fields.push_back({"observed", jsonBool(true), true});
    fields.push_back({"quality", qualityJson(state.quality), true});
    fields.push_back({"freshness", freshnessJson(state.freshness), true});
    if (state.hasClosedBar) {
        fields.push_back({"last_successful_update",
                          jsonInteger(state.lastClosedBar.openTimeSec), true});
    } else if (state.freshness.lastUpdate.isKnown()) {
        fields.push_back({"last_successful_update",
                          jsonInteger(state.freshness.lastUpdate.epochMillis()),
                          true});
    } else {
        fields.push_back({"last_successful_update", "null", true});
    }
    fields.push_back({"sequence",
                      jsonInteger(static_cast<std::int64_t>(state.sequence)), true});
    fields.push_back({"open", jsonNumber(state.lastClosedBar.open), true});
    fields.push_back({"high", jsonNumber(state.lastClosedBar.high), true});
    fields.push_back({"low", jsonNumber(state.lastClosedBar.low), true});
    fields.push_back({"close", jsonNumber(state.lastClosedBar.close), true});
    fields.push_back({"open_time",
                      jsonInteger(state.lastClosedBar.openTimeSec), true});
    fields.push_back({"capability_impact",
                      capabilityImpactJson(parsed, state.quality), true});
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::latestSignal() const {
    if (deps_.ledger == nullptr) return unavailable("prediction ledger");
    const auto& records = deps_.ledger->records();
    if (records.empty()) {
        std::vector<ApiField> fields;
        fields.push_back({"available", jsonBool(false), true});
        fields.push_back({"reason", "no decisions recorded yet"});
        return ApiResponse{200, "application/json",
                           envelope(jsonObject(fields)), true};
    }
    const PredictionRecord& record = records.back();
    std::vector<ApiField> fields;
    fields.push_back({"available", jsonBool(true), true});
    fields.push_back({"decision_id", record.decisionId.value()});
    fields.push_back({"trigger_timeframe", toString(record.timeframe)});
    fields.push_back({"closed_bar_id", jsonInteger(record.asOfBarOpenSec), true});
    fields.push_back({"signal", toString(record.direction)});
    fields.push_back({"score", jsonNumber(record.score), true});
    fields.push_back({"score_is_probability", jsonBool(false), true});
    fields.push_back({"confidence", jsonNumber(record.confidence), true});
    // Probability stays explicitly N/A until a calibration exists.
    fields.push_back({"probability", "null", true});
    fields.push_back({"probability_calibrated", jsonBool(false), true});
    fields.push_back({"reference_price", jsonNumber(record.referencePrice), true});
    fields.push_back({"system_mode", "SHADOW"});
    fields.push_back({"has_outcome", jsonBool(record.hasOutcome), true});

    // Freshest price seen, when a live tick has been read. `reference_price`
    // above is the decision's own bar close and is deliberately left as the
    // frozen contract value; this is the current market price beside it, so a
    // caller can see how far the entry level has drifted.
    double lastPrice = 0.0;
    std::int64_t lastPriceTime = 0;
    if (deps_.runtime != nullptr &&
        deps_.runtime->lastTickPrice(lastPrice, lastPriceTime)) {
        fields.push_back({"last_price", jsonNumber(lastPrice), true});
        fields.push_back({"last_price_time", jsonInteger(lastPriceTime), true});
    } else {
        fields.push_back({"last_price", "null", true});
        fields.push_back({"last_price_time", "null", true});
    }

    // Decision display contract: symbol, trigger time, data/structure/regime/
    // eligibility states, and version identity. These come from the live
    // decision context (the actual chain inputs), not from re-derivation.
    const DecisionContext context =
        deps_.runtime != nullptr ? deps_.runtime->lastDecisionContext()
                                 : DecisionContext{};
    if (context.available) {
        fields.push_back({"symbol", context.symbol});
        fields.push_back({"trigger_time",
                          jsonInteger(context.evaluatedAt.epochMillis()), true});
        fields.push_back({"data_state", toString(context.dataState)});
        fields.push_back({"structure_state", toString(context.structure)});
        fields.push_back({"regime_state", toString(context.regime)});
        fields.push_back({"eligibility_state", toString(context.eligibility)});
        fields.push_back({"strategy_version", context.strategyVersion});
        fields.push_back({"configuration_version", context.configurationVersion});
    } else {
        // The prediction exists but this process has no live decision context
        // (e.g. recovered ledger). Report the fields as unknown, not guessed.
        fields.push_back({"symbol", "null", true});
        fields.push_back({"trigger_time", "null", true});
        fields.push_back({"data_state", "UNKNOWN"});
        fields.push_back({"structure_state", "UNKNOWN"});
        fields.push_back({"regime_state", "UNKNOWN"});
        fields.push_back({"eligibility_state", "UNKNOWN"});
        fields.push_back({"strategy_version", "null", true});
        fields.push_back({"configuration_version", "null", true});
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::latestProbability() const {
    // RULE C: this route presents a probability ONLY when a calibration has
    // been produced AND independently audited. Absent the gate, or absent an
    // audited calibrated value, it reports `calibrated=false` and a null
    // probability. The raw score is never disguised as a probability.
    if (deps_.probability == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: probability surface");
    }
    return deps_.probability->latest(deps_.ledger);
}

ApiResponse BackendFacade::latestRisk() const {
    // Portfolio-level risk always comes from the live position simulator. The
    // per-decision proposal is reported from the last decision context (the
    // engine output); the facade never recomputes risk.
    if (deps_.positions == nullptr) return unavailable("position simulator");
    std::vector<ApiField> fields;
    fields.push_back({"available", jsonBool(true), true});
    fields.push_back({"open_positions", jsonInteger(
        static_cast<std::int64_t>(deps_.positions->openCount())), true});
    fields.push_back({"aggregate_open_risk_fraction",
                      jsonNumber(deps_.positions->aggregateOpenRiskFraction()), true});
    fields.push_back({"risk_bounded_by_guardian", jsonBool(true), true});

    const DecisionContext context =
        deps_.runtime != nullptr ? deps_.runtime->lastDecisionContext()
                                 : DecisionContext{};
    if (context.riskAvailable) {
        const RiskProposal& risk = context.risk;
        std::vector<ApiField> proposal;
        proposal.push_back({"decision_id", context.decisionId.value()});
        proposal.push_back({"direction", toString(risk.direction)});
        proposal.push_back({"entry_price", jsonNumber(risk.entryPrice), true});
        proposal.push_back({"stop_price", jsonNumber(risk.stopPrice), true});
        proposal.push_back({"target_price", jsonNumber(risk.targetPrice), true});
        proposal.push_back({"risk_fraction", jsonNumber(risk.riskFraction), true});
        proposal.push_back({"risk_amount", jsonNumber(risk.riskAmount), true});
        proposal.push_back({"position_size_lots",
                            jsonNumber(risk.positionSizeLots), true});
        proposal.push_back({"reward_risk_ratio",
                            jsonNumber(risk.rewardRiskRatio), true});
        proposal.push_back({"decision", toString(risk.decision)});
        proposal.push_back({"reason", risk.reason});
        proposal.push_back({"valid", jsonBool(risk.valid), true});
        fields.push_back({"proposal_available", jsonBool(true), true});
        fields.push_back({"proposal", jsonObject(proposal), true});
        fields.push_back({"proposal_reason", risk.reason});
    } else {
        // No decision has been evaluated in this process: the proposal is
        // explicitly unavailable, never fabricated.
        fields.push_back({"proposal_available", jsonBool(false), true});
        fields.push_back({"proposal", "null", true});
        fields.push_back({"proposal_reason", "no decision evaluated yet"});
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::shadowPositions() const {
    if (deps_.positions == nullptr) return unavailable("position simulator");
    std::vector<std::string> elements;
    for (const auto& position : deps_.positions->positions()) {
        std::vector<ApiField> fields;
        fields.push_back({"position_id", position.positionId.value()});
        fields.push_back({"decision_id", position.decisionId.value()});
        fields.push_back({"direction", toString(position.direction)});
        fields.push_back({"state", toString(position.state)});
        fields.push_back({"entry_price", jsonNumber(position.entryPrice), true});
        fields.push_back({"stop_price", jsonNumber(position.stopPrice), true});
        fields.push_back({"target_price", jsonNumber(position.targetPrice), true});
        fields.push_back({"lots", jsonNumber(position.lots), true});
        fields.push_back({"risk_fraction", jsonNumber(position.riskFraction), true});
        fields.push_back({"timeframe", toString(position.timeframe)});
        fields.push_back({"opened_at",
                          jsonInteger(position.openedAt.epochMillis()), true});
        fields.push_back({"opened_bar_open",
                          jsonInteger(position.openedBarOpenSec), true});
        fields.push_back({"exit_price", jsonNumber(position.exitPrice), true});
        fields.push_back({"realized_pnl", jsonNumber(position.realizedPnL), true});
        fields.push_back({"realized_r", jsonNumber(position.realizedR), true});
        fields.push_back({"close_reason", position.closeReason});
        if (position.state == PositionState::OPEN) {
            fields.push_back({"closed_at", "null", true});
            fields.push_back({"closed_bar_open", "null", true});
        } else {
            fields.push_back({"closed_at",
                              jsonInteger(position.closedAt.epochMillis()), true});
            fields.push_back({"closed_bar_open",
                              jsonInteger(position.closedBarOpenSec), true});
        }
        fields.push_back({"shadow_only", jsonBool(true), true});
        elements.push_back(jsonObject(fields));
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonArray(elements)), true};
}

ApiResponse BackendFacade::shadowOutcomes() const {
    // The outcome engine is the authoritative source of closed-outcome facts;
    // the ledger link is a secondary index. Prefer the engine when present.
    std::vector<std::string> elements;
    if (deps_.runtime != nullptr) {
        const auto& outcomes = deps_.runtime->outcomes().outcomes();
        for (const auto& outcome : outcomes) {
            std::vector<ApiField> fields;
            fields.push_back({"outcome_id", outcome.outcomeId.value()});
            fields.push_back({"position_id", outcome.positionId.value()});
            fields.push_back({"decision_id", outcome.decisionId.value()});
            fields.push_back({"direction", toString(outcome.direction)});
            fields.push_back({"timeframe", toString(outcome.timeframe)});
            fields.push_back({"exit_state", toString(outcome.exitState)});
            fields.push_back({"entry_price", jsonNumber(outcome.entryPrice), true});
            fields.push_back({"exit_price", jsonNumber(outcome.exitPrice), true});
            fields.push_back({"lots", jsonNumber(outcome.lots), true});
            fields.push_back({"realized_pnl", jsonNumber(outcome.realizedPnL), true});
            fields.push_back({"realized_r", jsonNumber(outcome.realizedR), true});
            fields.push_back({"risk_fraction", jsonNumber(outcome.riskFraction), true});
            fields.push_back({"bars_held", jsonInteger(outcome.barsHeld), true});
            fields.push_back({"outcome_class", toString(outcome.classification)});
            fields.push_back({"recorded_at",
                              jsonInteger(outcome.recordedAt.epochMillis()), true});
            fields.push_back({"note", outcome.note});
            fields.push_back({"shadow_only", jsonBool(true), true});
            elements.push_back(jsonObject(fields));
        }
    } else if (deps_.ledger != nullptr) {
        for (const auto& record : deps_.ledger->records()) {
            if (!record.hasOutcome) continue;
            std::vector<ApiField> fields;
            fields.push_back({"decision_id", record.decisionId.value()});
            fields.push_back({"outcome_id", record.outcomeId.value()});
            fields.push_back({"realized_r", jsonNumber(record.realizedR), true});
            fields.push_back({"outcome_class", toString(record.outcomeClass)});
            elements.push_back(jsonObject(fields));
        }
    } else {
        return unavailable("prediction ledger");
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonArray(elements)), true};
}

ApiResponse BackendFacade::researchStatus() const {
    std::vector<ApiField> fields;
    fields.push_back({"available", jsonBool(true), true});
    fields.push_back({"mode", "SHADOW"});
    fields.push_back({"note",
                      "research output never grants execution authority"});

    if (deps_.runtime != nullptr) {
        // Experiment history (append-only research ledger).
        std::vector<std::string> experiments;
        for (const auto& record : deps_.runtime->experiments().all()) {
            std::vector<ApiField> ef;
            ef.push_back({"experiment_id", record.experimentId.value()});
            ef.push_back({"hypothesis_id", record.hypothesisId.value()});
            ef.push_back({"method", record.method});
            ef.push_back({"outcome", toString(record.outcome)});
            ef.push_back({"sample_size",
                          jsonInteger(static_cast<std::int64_t>(record.sampleSize)),
                          true});
            ef.push_back({"result_metric", jsonNumber(record.resultMetric), true});
            ef.push_back({"started_at",
                          jsonInteger(record.startedAt.epochMillis()), true});
            experiments.push_back(jsonObject(ef));
        }
        fields.push_back({"experiment_count",
                          jsonInteger(static_cast<std::int64_t>(
                              deps_.runtime->experiments().size())),
                          true});
        fields.push_back({"experiments", jsonArray(experiments), true});

        // Failure memory (recovery history), open failures surfaced.
        std::vector<std::string> failures;
        for (const auto& record : deps_.runtime->failures().all()) {
            std::vector<ApiField> ff;
            ff.push_back({"failure_id", record.failureId.value()});
            ff.push_back({"category", toString(record.category)});
            ff.push_back({"summary", record.summary});
            ff.push_back({"occurrences",
                          jsonInteger(static_cast<std::int64_t>(
                              record.occurrences)),
                          true});
            ff.push_back({"resolved", jsonBool(record.resolved), true});
            ff.push_back({"last_seen",
                          jsonInteger(record.lastSeen.epochMillis()), true});
            failures.push_back(jsonObject(ff));
        }
        fields.push_back({"failure_count",
                          jsonInteger(static_cast<std::int64_t>(
                              deps_.runtime->failures().size())),
                          true});
        fields.push_back({"failures", jsonArray(failures), true});
    } else {
        fields.push_back({"experiment_count", jsonInteger(0), true});
        fields.push_back({"experiments", jsonArray({}), true});
        fields.push_back({"failure_count", jsonInteger(0), true});
        fields.push_back({"failures", jsonArray({}), true});
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::governanceStatus() const {
    if (deps_.approvals == nullptr) return unavailable("approval gate");
    std::vector<std::string> pending;
    for (const auto& request : deps_.approvals->pending()) {
        std::vector<ApiField> fields;
        fields.push_back({"request_id", request.requestId.value()});
        fields.push_back({"kind", toString(request.kind)});
        fields.push_back({"subject_id", request.subjectId.value()});
        fields.push_back({"requested_at",
                          jsonInteger(request.requestedAt.epochMillis()), true});
        pending.push_back(jsonObject(fields));
    }

    // Full request history, so the frontend can show decided/withdrawn items,
    // not only the pending queue.
    std::vector<std::string> history;
    for (const auto& request : deps_.approvals->all()) {
        std::vector<ApiField> hf;
        hf.push_back({"request_id", request.requestId.value()});
        hf.push_back({"kind", toString(request.kind)});
        hf.push_back({"status", toString(request.status)});
        hf.push_back({"requested_by", request.requestedBy});
        hf.push_back({"requested_at",
                      jsonInteger(request.requestedAt.epochMillis()), true});
        if (request.decidedAt.isKnown()) {
            hf.push_back({"decided_at",
                          jsonInteger(request.decidedAt.epochMillis()), true});
        } else {
            hf.push_back({"decided_at", "null", true});
        }
        history.push_back(jsonObject(hf));
    }

    std::vector<ApiField> fields;
    fields.push_back({"pending_count", jsonInteger(
        static_cast<std::int64_t>(pending.size())), true});
    fields.push_back({"pending", jsonArray(pending), true});
    fields.push_back({"history", jsonArray(history), true});
    fields.push_back({"live_trading_authorised", jsonBool(false), true});
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::bridgeStatus() const {
    if (deps_.runtime == nullptr) return unavailable("runtime");
    std::vector<ApiField> fields;
    fields.push_back({"bridge_process_state",
                      toString(deps_.runtime->bridgeState())});
    fields.push_back({"startup_stage",
                      toString(deps_.runtime->startupStage())});
    fields.push_back({"handshake_ok",
                      jsonBool(deps_.runtime->bridgeHandshakeOk()), true});
    fields.push_back({"mt5_ready", jsonBool(deps_.runtime->mt5Ready()), true});
    fields.push_back({"resolved_symbol", deps_.runtime->resolvedSymbol()});
    fields.push_back({"transport", "http_loopback"});
    fields.push_back({"host", "127.0.0.1"});
    fields.push_back({"loopback_only", jsonBool(true), true});
    fields.push_back({"managed_by_application", jsonBool(true), true});
    fields.push_back({"requires_manual_cmd", jsonBool(false), true});

    BridgeHealth health;
    if (deps_.runtime->lastBridgeHealth(health)) {
        fields.push_back({"package_available", jsonBool(health.packageAvailable), true});
        fields.push_back({"initialized", jsonBool(health.initialized), true});
        fields.push_back({"mt5_ready_live", jsonBool(health.mt5Ready), true});
        fields.push_back({"broker", health.broker});
        fields.push_back({"server", health.server});
        fields.push_back({"bridge_symbol", health.resolvedSymbol});
        fields.push_back({"process_state", health.processState});
        fields.push_back({"last_error", health.lastError});
        fields.push_back({"last_successful_request",
                          jsonInteger(health.lastSuccessfulRequest.epochMillis()),
                          true});
        fields.push_back({"observed", jsonBool(true), true});
    } else {
        // Bridge unreachable: report unknown, never a fabricated broker.
        fields.push_back({"package_available", jsonBool(false), true});
        fields.push_back({"initialized", jsonBool(false), true});
        fields.push_back({"mt5_ready_live", jsonBool(false), true});
        fields.push_back({"broker", "null", true});
        fields.push_back({"server", "null", true});
        fields.push_back({"bridge_symbol", "null", true});
        fields.push_back({"process_state", "OFFLINE"});
        fields.push_back({"last_error", "bridge health not observed"});
        fields.push_back({"last_successful_request", "null", true});
        fields.push_back({"observed", jsonBool(false), true});
    }
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::recentAudit() const {
    // The append-only audit stream is the authoritative record; incidents are a
    // separate, related view. Both are surfaced here, clearly separated.
    if (deps_.incidents == nullptr) return unavailable("incident manager");

    std::vector<ApiField> fields;

    std::vector<std::string> records;
    std::int64_t sequence = 0;
    if (deps_.runtime != nullptr) {
        const auto& all = deps_.runtime->audit().records();
        // Newest first, bounded so the response stays frontend-sized.
        const std::size_t limit = 200;
        const std::size_t count = all.size() < limit ? all.size() : limit;
        for (std::size_t i = 0; i < count; ++i) {
            const AuditRecord& record = all[all.size() - 1 - i];
            std::vector<ApiField> af;
            af.push_back({"sequence",
                          jsonInteger(static_cast<std::int64_t>(record.sequence)),
                          true});
            af.push_back({"event_id", record.eventId.value()});
            af.push_back({"action", toString(record.action)});
            af.push_back({"outcome", toString(record.outcome)});
            af.push_back({"service_state", toString(record.serviceState)});
            af.push_back({"occurred_at",
                          jsonInteger(record.occurredAt.epochMillis()), true});
            af.push_back({"actor", record.actor});
            af.push_back({"subject", record.subject});
            af.push_back({"details", record.details});
            af.push_back({"previous_hash", record.previousHash.toString()});
            af.push_back({"record_hash", record.recordHash.toString()});
            records.push_back(jsonObject(af));
        }
        sequence = static_cast<std::int64_t>(all.size());
    }

    std::vector<std::string> active;
    for (const auto& incident : deps_.incidents->active()) {
        std::vector<ApiField> inc;
        inc.push_back({"incident_id", incident.incidentId.value()});
        inc.push_back({"severity", toString(incident.severity)});
        inc.push_back({"state", toString(incident.state)});
        inc.push_back({"title", incident.title});
        active.push_back(jsonObject(inc));
    }

    fields.push_back({"audit_stream_size", jsonInteger(sequence), true});
    fields.push_back({"audit_records", jsonArray(records), true});
    fields.push_back({"active_incidents", jsonArray(active), true});
    fields.push_back({"count", jsonInteger(sequence), true});
    return ApiResponse{200, "application/json",
                       envelope(jsonObject(fields)), true};
}

ApiResponse BackendFacade::handle(const std::string& method,
                                  const std::string& path) const {
    return handle(method, path, "");
}

ApiResponse BackendFacade::latestAnalysis() const {
    if (deps_.analysis == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: analysis surface");
    }
    const std::string symbol =
        deps_.runtime != nullptr ? deps_.runtime->resolvedSymbol() : std::string();
    const DecisionContext context =
        deps_.runtime != nullptr ? deps_.runtime->lastDecisionContext()
                                 : DecisionContext{};
    return deps_.analysis->latest(symbol, context, deps_.probability, deps_.ledger);
}

ApiResponse BackendFacade::analysisHistory(int limit) const {
    if (deps_.analysis == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: analysis surface");
    }
    const std::string symbol =
        deps_.runtime != nullptr ? deps_.runtime->resolvedSymbol() : std::string();
    const DecisionContext context =
        deps_.runtime != nullptr ? deps_.runtime->lastDecisionContext()
                                 : DecisionContext{};
    return deps_.analysis->history(symbol, context, deps_.probability, deps_.ledger,
                                   limit);
}

ApiResponse BackendFacade::contextLatest() const {
    if (deps_.analysis == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: analysis surface");
    }
    const std::string symbol =
        deps_.runtime != nullptr ? deps_.runtime->resolvedSymbol() : std::string();
    const DecisionContext context =
        deps_.runtime != nullptr ? deps_.runtime->lastDecisionContext()
                                 : DecisionContext{};
    return deps_.analysis->contextLatest(symbol, context);
}

ApiResponse BackendFacade::healthV1() const {
    if (deps_.analysis == nullptr) {
        return errorResponse(503, "dependency_unavailable",
                             "backend component unavailable: analysis surface");
    }
    std::string bridgeState = "OFFLINE";
    bool handshakeOk = false;
    if (deps_.runtime != nullptr) {
        bridgeState = toString(deps_.runtime->bridgeState());
        handshakeOk = deps_.runtime->bridgeHandshakeOk();
    }
    std::int64_t uptimeSec = -1;
    if (startedAtSec_ >= 0) {
        const std::int64_t nowSec = Timestamp::now().epochMillis() / 1000;
        uptimeSec = nowSec >= startedAtSec_ ? nowSec - startedAtSec_ : 0;
    }
    return deps_.analysis->health(deps_.health, bridgeState, handshakeOk, uptimeSec);
}

ApiResponse BackendFacade::handle(const std::string& method,
                                  const std::string& path,
                                  const std::string& query) const {
    if (method != "GET") {
        return errorResponse(405, "method_not_allowed",
                             "only GET is supported on read routes");
    }
    if (path == "/api/v1/system/state") return systemState();
    if (path == "/api/v1/health") return health();
    if (path == "/api/v1/timeframes") return timeframes();
    if (path == "/api/v1/signals/latest") return latestSignal();
    if (path == "/api/v1/probability/latest") return latestProbability();
    if (path == "/api/v1/risk/latest") return latestRisk();
    if (path == "/api/v1/shadow/positions") return shadowPositions();
    if (path == "/api/v1/shadow/outcomes") return shadowOutcomes();
    if (path == "/api/v1/research/status") return researchStatus();
    if (path == "/api/v1/governance/status") return governanceStatus();
    if (path == "/api/v1/bridge/status") return bridgeStatus();
    if (path == "/api/v1/audit/recent") return recentAudit();

    // Decision-support analysis surface (Phase 4.0 / T16).
    if (path == "/api/v1/analysis/latest") return latestAnalysis();
    if (path == "/api/v1/analysis/history") {
        int limit = 50;
        const std::string key = "limit=";
        const std::size_t at = query.find(key);
        if (at != std::string::npos) {
            std::size_t end = query.find('&', at);
            const std::string value =
                query.substr(at + key.size(),
                             end == std::string::npos ? std::string::npos
                                                      : end - (at + key.size()));
            try {
                limit = std::stoi(value);
            } catch (...) {
                limit = 50;  // malformed limit falls back to the default
            }
        }
        if (limit < 0) limit = 0;
        if (limit > 500) limit = 500;
        return analysisHistory(limit);
    }
    if (path == "/api/v1/context/latest") return contextLatest();
    if (path == "/api/v1/health/v1") return healthV1();

    const std::string prefix = "/api/v1/timeframes/";
    if (path.size() > prefix.size() &&
        path.compare(0, prefix.size(), prefix) == 0) {
        const std::string tail = path.substr(prefix.size());
        const std::string suffix = "/snapshot";
        if (tail.size() > suffix.size() &&
            tail.compare(tail.size() - suffix.size(), suffix.size(), suffix) == 0) {
            const std::string timeframe =
                tail.substr(0, tail.size() - suffix.size());
            return timeframeSnapshot(timeframe);
        }
    }
    return errorResponse(404, "not_found", "unknown route: " + path);
}

ApiResponse BackendFacade::command(const CommandRequest& request) {
    // Allow-list only. There is deliberately no command that can enable live
    // execution or bypass policy.
    const std::vector<std::string> allowed = {"notify", "request_approval"};
    bool permitted = false;
    for (const auto& name : allowed) {
        if (request.command == name) permitted = true;
    }
    if (!permitted) {
        return errorResponse(403, "command_not_permitted",
                             "command is not on the allow-list: " +
                                 request.command);
    }
    if (request.actor.empty()) {
        return errorResponse(400, "actor_required",
                             "every command must be attributable to an actor");
    }

    if (request.command == "notify") {
        if (deps_.telegram == nullptr) return unavailable("telegram gateway");
        const EntityId id =
            deps_.telegram->enqueue("", request.payload, Timestamp::now());
        if (id.empty()) {
            return errorResponse(429, "queue_full",
                                 "notification queue is full");
        }
        commandLog_.push_back(request.command + ":" + id.value());
        std::vector<ApiField> fields;
        fields.push_back({"accepted", jsonBool(true), true});
        fields.push_back({"message_id", id.value()});
        return ApiResponse{202, "application/json",
                           envelope(jsonObject(fields)), true};
    }

    // request_approval
    if (deps_.approvals == nullptr) return unavailable("approval gate");
    const EntityId id = deps_.approvals->request(
        ApprovalRequestKind::POLICY_CHANGE, EntityId(), request.actor,
        request.payload, Timestamp::now());
    commandLog_.push_back(request.command + ":" + id.value());
    std::vector<ApiField> fields;
    fields.push_back({"accepted", jsonBool(true), true});
    fields.push_back({"request_id", id.value()});
    fields.push_back({"status", toString(ApprovalStatus::PENDING)});
    return ApiResponse{202, "application/json",
                       envelope(jsonObject(fields)), true};
}

}  // namespace aura
