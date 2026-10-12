// HOST-0010 - Backend runtime implementation.

#include "runtime/AuraRuntime.h"

#include "data/BarFinalizer.h"

namespace aura {

AuraRuntime::AuraRuntime(StartupOptions startupOptions, int candlesPerTimeframe)
    : startupOptions_(std::move(startupOptions)),
      startup_(startupOptions_),
      candlesPerTimeframe_(candlesPerTimeframe),
      guardian_(makeGuardian()) {}

bool AuraRuntime::start(std::string& error) {
    ready_ = false;
    mode_ = SystemMode::STARTING;

    const bool started = startup_.run(startupReport_);
    auto client = startup_.bridgeClient();
    if (client) {
        ingestor_ = std::make_unique<MarketDataIngestor>(client);
    }

    // Durable state lives under the resolved data directory. When paths are
    // unavailable (unresolved executable) the pipeline still runs, but nothing
    // is persisted and that is reported rather than faked.
    if (!startupReport_.paths.dataDir.empty()) {
        store_ = std::make_unique<FilePersistenceStore>(startupReport_.paths.dataDir);
        if (store_->isAvailable()) {
            persistence_ = std::make_unique<PersistenceEngine>(store_.get());
        } else {
            store_.reset();
        }
    }
    // Research memory is durable and derived from outcomes/failures only. It
    // grants no execution authority.
    experiments_ = ExperimentLedger(store_.get());
    failures_ = FailureMemory(store_.get());
    pipeline_ = std::make_unique<DecisionPipeline>(
        guardian_.get(), &ledger_, persistence_.get(), &positions_, &outcomes_);
    pipeline_->setSymbol(startupReport_.resolvedSymbol.empty()
                             ? startupOptions_.preferredSymbol
                             : startupReport_.resolvedSymbol);

    if (!started) {
        // The runtime is allowed to exist in DEGRADED mode without the bridge;
        // it simply cannot ingest. This is explicit, not silent.
        ready_ = false;
        mode_ = startupReport_.stage == StartupStage::FAILED ? SystemMode::EMERGENCY
                                                             : SystemMode::DEGRADED;
        error = startupReport_.error;
        return false;
    }

    ready_ = true;
    mode_ = SystemMode::SHADOW;   // shadow-first product mode
    error.clear();
    return true;
}

RuntimeCycleReport AuraRuntime::tick(Timestamp now) {
    RuntimeCycleReport report;
    report.at = now;

    if (!ingestor_) {
        report.issues.push_back("no ingestor (bridge unavailable)");
        if (ready_) {
            mode_ = SystemMode::DEGRADED;
        }
        return report;
    }

    for (Timeframe timeframe : allTimeframes()) {
        IngestRequest request;
        request.symbol = startupReport_.resolvedSymbol.empty()
                             ? startupOptions_.preferredSymbol
                             : startupReport_.resolvedSymbol;
        request.timeframe = timeframe;
        request.count = candlesPerTimeframe_;
        request.closedOnly = true;

        IngestResult result = ingestor_->ingest(request, now);
        report.qualityByTimeframe[toString(timeframe)] = result.quality;
        if (!result.ok) {
            ++report.timeframesFailed;
            for (const auto& issue : result.issues) {
                report.issues.push_back(std::string(toString(timeframe)) + ": " + issue);
            }
            timeframeStore_.refreshFreshness(timeframe, now);
            continue;
        }

        // Only fully-closed bars advance timeframe state.
        BarFinalizer finalizer;
        FinalizedBars finalized = finalizer.finalize(result.bars, now);
        if (finalized.closedBars.empty()) {
            ++report.timeframesFailed;
            report.issues.push_back(std::string(toString(timeframe)) +
                                    ": no closed bars available");
            timeframeStore_.refreshFreshness(timeframe, now);
            continue;
        }

        // The bridge returns a rolling window, so each tick overlaps the last.
        // Publish only bars strictly newer than the last published bar for the
        // timeframe; re-publishing a closed bar would duplicate history.
        const std::string key = toString(timeframe);
        const std::int64_t lastPublished = lastPublishedBarOpenSec_[key];
        std::uint64_t lastSequence = dataBus_.sequence(timeframe);
        const Bar* newest = nullptr;
        for (const auto& bar : finalized.closedBars) {
            if (bar.openTimeSec <= lastPublished) continue;
            lastSequence = dataBus_.publish(bar, "mt5-python-bridge", now);
            lastPublishedBarOpenSec_[key] = bar.openTimeSec;
            newest = &bar;
        }
        if (newest == nullptr) {
            // No new closed bar this cycle; refresh freshness and move on.
            timeframeStore_.refreshFreshness(timeframe, now);
            ++report.timeframesIngested;
            continue;
        }
        timeframeStore_.updateFromClosedBar(*newest, lastSequence);
        timeframeStore_.refreshFreshness(timeframe, now);
        ++report.timeframesIngested;

        // The operational timeframe drives the live decision pipeline. Other
        // timeframes only advance open-position simulation when relevant.
        if (timeframe == pipeline_->config().operationalTimeframe) {
            runDecisionCycle(*newest, now, report);
        }
        if (positions_.openCount() > 0) {
            std::vector<EntityId> closedOutcomes;
            pipeline_->advancePositions(*newest, now, report.issues,
                                        &closedOutcomes);
            for (const auto& outcomeId : closedOutcomes) {
                appendAudit(AuditAction::OUTCOME_RECORDED, outcomeId.value(),
                            "outcome recorded from closed shadow position", now);
            }
        }
    }

    // Refresh the health view so the API layer reports the current state
    // rather than a stale one. Health observation is non-fatal.
    health_.observeTimeframes(timeframeStore_, now);
    if (auto client = bridge()) {
        health_.observeBridge(*client, now);
        const auto bridgeHealth = client->health();
        if (bridgeHealth.ok) {
            lastBridgeHealth_ = bridgeHealth.value;
            lastBridgeHealthValid_ = true;
        } else {
            lastBridgeHealthValid_ = false;
        }

        // Freshest price the engine can see. The candle series only moves at
        // bar close, so a tick read is what makes the reported price current
        // rather than up to one interval old. A failed read leaves the last
        // good value in place and never invents one.
        const std::string tickSymbol =
            startupReport_.resolvedSymbol.empty()
                ? startupOptions_.preferredSymbol
                : startupReport_.resolvedSymbol;
        const auto tickResult = client->tick(tickSymbol);
        if (tickResult.ok) {
            const BridgeTick& tick = tickResult.value;
            const double price = tick.last > 0.0 ? tick.last
                               : (tick.bid > 0.0 ? tick.bid : tick.ask);
            if (price > 0.0) {
                lastTickPrice_ = price;
                lastTickTimeSec_ = tick.time;
                lastTickPriceValid_ = true;
            }
        }
    }

    // Operating mode reflects data availability. M15 is the primary
    // operational timeframe; if it is not fresh, the system is not SHADOW.
    const DataQualityState m15 = timeframeStore_.qualityOf(Timeframe::M15);
    if (!isDecisionGrade(m15)) {
        mode_ = SystemMode::DEGRADED;
    } else if (report.timeframesFailed == 0) {
        mode_ = SystemMode::SHADOW;
    } else {
        mode_ = SystemMode::DEGRADED;
    }
    return report;
}

void AuraRuntime::runDecisionCycle(const Bar& closedBar, Timestamp now,
                                   RuntimeCycleReport& report) {
    if (!pipeline_) return;
    const std::string key = toString(closedBar.timeframe);
    if (closedBar.openTimeSec <= lastDecisionBarOpenSec_[key]) {
        return;   // this bar was already evaluated
    }

    const std::vector<Bar> history = dataBus_.recent(
        closedBar.timeframe, pipeline_->config().historyBars);
    const PipelineCycleReport decision =
        pipeline_->onClosedBar(closedBar, history, now);
    lastDecisionBarOpenSec_[key] = closedBar.openTimeSec;

    if (decision.decisionProduced && !decision.decisions.empty()) {
        appendAudit(AuditAction::DECISION_PRODUCED,
                    decision.decisions.front().decisionId.value(),
                    "decision evaluated on closed bar", now);
    }
    if (decision.shadowIssued) {
        report.shadowCommandsIssued += 1;
        if (!decision.decisions.empty()) {
            appendAudit(AuditAction::SHADOW_COMMAND_ISSUED,
                        decision.decisions.front().decisionId.value(),
                        "shadow command issued", now);
        }
    }
    for (const auto& issue : decision.issues) {
        report.issues.push_back(key + ": " + issue);
    }
}

void AuraRuntime::appendAudit(AuditAction action, const std::string& subject,
                              const std::string& details, Timestamp now) {
    AuditRecord record;
    record.eventId = EntityId(std::string(toString(action)) + "-" +
                              std::to_string(audit_.size() + 1));
    record.action = action;
    record.outcome = AuditOutcome::SUCCESS;
    record.serviceState = ServiceState::ONLINE;
    record.occurredAt = now;
    record.actor = "aura-runtime";
    record.subject = subject;
    record.details = details;
    audit_.append(record);
}

void AuraRuntime::stop() {
    startup_.shutdown();
    ready_ = false;
    mode_ = SystemMode::HALTED;
}

bool AuraRuntime::bridgeHealth(BridgeHealth& out) const {
    auto client = const_cast<StartupCoordinator&>(startup_).bridgeClient();
    if (!client) return false;
    const auto result = client->health();
    if (!result.ok) return false;
    out = result.value;
    return true;
}

ServiceState AuraRuntime::bridgeState() const noexcept {
    ProcessSupervisor* supervisor = const_cast<StartupCoordinator&>(startup_).supervisor();
    if (supervisor == nullptr) return ServiceState::OFFLINE;
    return supervisor->state();
}

std::map<std::string, std::string> AuraRuntime::healthSummary() const {
    std::map<std::string, std::string> summary;
    summary["mode"] = toString(mode_);
    summary["ready"] = ready_ ? "true" : "false";
    summary["startup_stage"] = toString(startupReport_.stage);
    summary["bridge_process"] = toString(bridgeState());
    summary["bridge_handshake"] = startupReport_.handshakeOk ? "OK" : "NOT_OK";
    summary["mt5_ready"] = startupReport_.mt5Ready ? "true" : "false";
    summary["resolved_symbol"] = startupReport_.resolvedSymbol;
    for (Timeframe timeframe : allTimeframes()) {
        summary[std::string("quality_") + toString(timeframe)] =
            toString(timeframeStore_.qualityOf(timeframe));
    }
    return summary;
}

}  // namespace aura
