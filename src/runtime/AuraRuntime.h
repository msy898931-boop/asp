#pragma once
// HOST-0009 - Top-level backend runtime.
//
// Owns the wiring between startup, ingestion, the data bus, and per-timeframe
// state. It exposes a single tick() that ingests closed bars for all
// timeframes; a failure in one timeframe never aborts the others.

#include "audit/AuditLog.h"
#include "data/DataBus.h"
#include "data/MarketDataIngestor.h"
#include "data/TimeframeStateStore.h"
#include "foundation/ServiceState.h"
#include "foundation/SystemMode.h"
#include "foundation/Timestamp.h"
#include "research/ExperimentLedger.h"
#include "research/FailureMemory.h"
#include "guardian/IGuardian.h"
#include "health/HealthMonitor.h"
#include "ledger/PredictionLedger.h"
#include "outcomes/OutcomeEngine.h"
#include "persistence/FilePersistenceStore.h"
#include "persistence/PersistenceEngine.h"
#include "platform/windows/StartupCoordinator.h"
#include "runtime/DecisionPipeline.h"
#include "shadow/PositionSimulator.h"

#include <map>
#include <memory>
#include <string>
#include <vector>

namespace aura {

struct RuntimeCycleReport {
    Timestamp at;
    int timeframesIngested = 0;
    int timeframesFailed = 0;
    int shadowCommandsIssued = 0;
    std::map<std::string, DataQualityState> qualityByTimeframe;
    std::vector<std::string> issues;
};

class AuraRuntime {
public:
    AuraRuntime(StartupOptions startupOptions = {},
                int candlesPerTimeframe = 500);

    // Run startup. Returns true when the runtime reached READY.
    bool start(std::string& error);

    // Ingest one cycle across all canonical timeframes. Never throws; reports.
    RuntimeCycleReport tick(Timestamp now);

    void stop();

    bool isReady() const noexcept { return ready_; }
    SystemMode mode() const noexcept { return mode_; }
    ServiceState bridgeState() const noexcept;

    const StartupReport& startupReport() const noexcept { return startupReport_; }
    TimeframeStateStore& timeframeStore() noexcept { return timeframeStore_; }
    const TimeframeStateStore& timeframeStore() const noexcept { return timeframeStore_; }
    DataBus& dataBus() noexcept { return dataBus_; }
    std::shared_ptr<IPythonBridgeClient> bridge() const noexcept {
        return startup_.bridgeClient();
    }

    // Decision/shadow surface. The facade reads these; nothing else mutates
    // them outside the runtime cycle.
    PredictionLedger& ledger() noexcept { return ledger_; }
    const PredictionLedger& ledger() const noexcept { return ledger_; }
    PositionSimulator& positions() noexcept { return positions_; }
    const PositionSimulator& positions() const noexcept { return positions_; }
    OutcomeEngine& outcomes() noexcept { return outcomes_; }
    const OutcomeEngine& outcomes() const noexcept { return outcomes_; }
    AuditLog& audit() noexcept { return audit_; }
    const AuditLog& audit() const noexcept { return audit_; }
    ExperimentLedger& experiments() noexcept { return experiments_; }
    const ExperimentLedger& experiments() const noexcept { return experiments_; }
    FailureMemory& failures() noexcept { return failures_; }
    const FailureMemory& failures() const noexcept { return failures_; }
    HealthMonitor& health() noexcept { return health_; }
    const HealthMonitor& health() const noexcept { return health_; }
    IGuardian* guardian() noexcept { return guardian_.get(); }
    const IGuardian* guardian() const noexcept { return guardian_.get(); }

    // Metadata for the most recent decision (structure/regime/eligibility and
    // risk proposal). Available == false until a decision is produced.
    DecisionContext lastDecisionContext() const {
        return pipeline_ != nullptr ? pipeline_->lastDecisionContext()
                                    : DecisionContext{};
    }
    std::string resolvedSymbol() const { return startupReport_.resolvedSymbol; }
    bool mt5Ready() const noexcept { return startupReport_.mt5Ready; }
    bool bridgeHandshakeOk() const noexcept { return startupReport_.handshakeOk; }
    StartupStage startupStage() const noexcept { return startupReport_.stage; }

    // Bridge identity/health, queried live from the loopback bridge client.
    // Returns false (with `out` left default) when the bridge is unavailable;
    // it never fabricates broker/MT5 detail.
    bool bridgeHealth(BridgeHealth& out) const;

    // Last bridge health observed during a tick. Read-only and non-blocking,
    // for the API layer. Returns false when no observation has succeeded.
    bool lastBridgeHealth(BridgeHealth& out) const {
        if (!lastBridgeHealthValid_) return false;
        out = lastBridgeHealth_;
        return true;
    }

    // Most recent live tick read from the bridge this cycle. The candle series
    // is only sampled at bar close, so this is the freshest price the engine
    // has seen. Returns false (leaving `out` untouched) when no tick has been
    // read; it never fabricates a price.
    bool lastTickPrice(double& out) const {
        if (!lastTickPriceValid_) return false;
        out = lastTickPrice_;
        return true;
    }
    bool lastTickPrice(double& out, std::int64_t& timeSec) const {
        if (!lastTickPriceValid_) return false;
        out = lastTickPrice_;
        timeSec = lastTickTimeSec_;
        return true;
    }

    // Health summary for the frontend/API layer.
    std::map<std::string, std::string> healthSummary() const;

private:
    // Runs the live decision pipeline for a freshly closed operational bar.
    void runDecisionCycle(const Bar& closedBar, Timestamp now,
                          RuntimeCycleReport& report);

    // Append an audit record. Best-effort: audit failure never aborts a cycle.
    void appendAudit(AuditAction action, const std::string& subject,
                     const std::string& details, Timestamp now);

    StartupOptions startupOptions_;
    StartupCoordinator startup_;
    StartupReport startupReport_;
    int candlesPerTimeframe_;
    DataBus dataBus_;
    TimeframeStateStore timeframeStore_;
    std::unique_ptr<MarketDataIngestor> ingestor_;

    std::unique_ptr<IGuardian> guardian_;
    std::unique_ptr<FilePersistenceStore> store_;
    std::unique_ptr<PersistenceEngine> persistence_;
    std::unique_ptr<DecisionPipeline> pipeline_;
    PredictionLedger ledger_;
    PositionSimulator positions_;
    OutcomeEngine outcomes_;
    HealthMonitor health_;
    AuditLog audit_;
    ExperimentLedger experiments_;
    FailureMemory failures_;
    BridgeHealth lastBridgeHealth_;
    bool lastBridgeHealthValid_ = false;

    // Newest live tick observed this cycle (see lastTickPrice).
    double lastTickPrice_ = 0.0;
    std::int64_t lastTickTimeSec_ = 0;
    bool lastTickPriceValid_ = false;

    // Last bar published per timeframe, so an overlapping bridge window does
    // not duplicate closed-bar history.
    std::map<std::string, std::int64_t> lastPublishedBarOpenSec_;

    // Last operational bar evaluated per timeframe, so a re-tick does not
    // re-evaluate an already-processed bar.
    std::map<std::string, std::int64_t> lastDecisionBarOpenSec_;

    bool ready_ = false;
    SystemMode mode_ = SystemMode::STARTING;
};

}  // namespace aura
