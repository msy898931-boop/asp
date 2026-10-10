#pragma once
// HOST-0007 - Coordinates deterministic application startup.
//
// Order: resolve paths -> locate bundle -> launch bridge -> wait for
// handshake -> expose readiness. Any failure is surfaced with a specific
// reason; startup never silently degrades into "just run python manually".

#include "foundation/ServiceState.h"
#include "foundation/Timestamp.h"
#include "mt5/PythonBridgeClient.h"
#include "platform/windows/BundleLocator.h"
#include "platform/windows/PathResolver.h"
#include "platform/windows/ProcessSupervisor.h"

#include <memory>
#include <string>

namespace aura {

enum class StartupStage {
    NOT_STARTED,
    RESOLVING_PATHS,
    LOCATING_BUNDLE,
    LAUNCHING_BRIDGE,
    WAITING_FOR_HANDSHAKE,
    READY,
    DEGRADED,       // bridge not ready but backend can still run safely
    FAILED,
};

inline const char* toString(StartupStage stage) noexcept {
    switch (stage) {
        case StartupStage::NOT_STARTED:          return "NOT_STARTED";
        case StartupStage::RESOLVING_PATHS:      return "RESOLVING_PATHS";
        case StartupStage::LOCATING_BUNDLE:      return "LOCATING_BUNDLE";
        case StartupStage::LAUNCHING_BRIDGE:     return "LAUNCHING_BRIDGE";
        case StartupStage::WAITING_FOR_HANDSHAKE:return "WAITING_FOR_HANDSHAKE";
        case StartupStage::READY:                return "READY";
        case StartupStage::DEGRADED:             return "DEGRADED";
        case StartupStage::FAILED:               return "FAILED";
    }
    return "FAILED";
}

struct StartupOptions {
    bool allowSystemPythonFallback = false;  // development only
    int handshakeTimeoutMillis = 15000;
    int handshakePollMillis = 250;
    std::string preferredSymbol = "XAUUSD";
    // Which market-data source the bridge serves: "mt5" (a Windows terminal),
    // "live" (keyless network feed), or "replay" (the synthetic corpus). The
    // packaged host defaults to "mt5"; a host with no terminal sets "live".
    std::string dataSource = "mt5";
    bool launchBridgeProcess = true;
};

struct StartupReport {
    StartupStage stage = StartupStage::NOT_STARTED;
    bool ready = false;
    bool bridgeProcessRunning = false;
    bool handshakeOk = false;
    bool mt5Ready = false;
    AppPaths paths;
    BundleLayout bundle;
    std::string resolvedSymbol;
    std::string error;
    std::string detail;
    Timestamp completedAt;
};

class StartupCoordinator {
public:
    StartupCoordinator(StartupOptions options = {}) : options_(std::move(options)) {}

    // Runs the full startup sequence. Returns true when READY.
    bool run(StartupReport& report);

    // Attempt bridge recovery after a failure; bounded by the supervisor.
    bool recover(StartupReport& report);

    void shutdown();

    const StartupReport& report() const noexcept { return report_; }
    std::shared_ptr<IPythonBridgeClient> bridgeClient() const noexcept {
        return bridgeClient_;
    }
    ProcessSupervisor* supervisor() noexcept { return supervisor_.get(); }

private:
    bool waitForHandshake(StartupReport& report);

    StartupOptions options_;
    StartupReport report_;
    PathResolver resolver_;
    std::unique_ptr<ProcessSupervisor> supervisor_;
    std::shared_ptr<IPythonBridgeClient> bridgeClient_;
};

}  // namespace aura
