// HOST-0008 - Startup coordinator implementation.

#include "platform/windows/StartupCoordinator.h"

#include <chrono>
#include <thread>

namespace aura {

bool StartupCoordinator::run(StartupReport& report) {
    report = StartupReport{};
    report.stage = StartupStage::RESOLVING_PATHS;

    std::string error;
    if (!resolver_.resolve(report.paths, error)) {
        report.stage = StartupStage::FAILED;
        report.error = error;
        report_ = report;
        return false;
    }

    report.stage = StartupStage::LOCATING_BUNDLE;
    BundleLocator locator(report.paths);
    report.bundle = locator.locate(options_.allowSystemPythonFallback);
    if (!report.bundle.found) {
        // The backend can still run without the bridge (no market data), but
        // the condition must be explicit, not silent.
        report.stage = StartupStage::DEGRADED;
        report.error = report.bundle.error;
        report.detail = "bridge bundle unavailable; backend starts without market data";
        report.completedAt = Timestamp::now();
        report_ = report;
        return false;
    }

    report.stage = StartupStage::LAUNCHING_BRIDGE;
    if (options_.launchBridgeProcess) {
        ProcessLaunchSpec spec;
        spec.executable = report.bundle.pythonExecutable;
        spec.args = {report.bundle.bridgeScript,
                     "--host", "127.0.0.1",
                     "--port", "8791",
                     "--symbol", options_.preferredSymbol,
                     "--data-source", options_.dataSource};
        spec.workingDir = report.paths.bridgeDir;
        spec.environment["PYTHONUNBUFFERED"] = "1";
        supervisor_ = std::make_unique<ProcessSupervisor>(spec);
        if (!supervisor_->start(error)) {
            report.stage = StartupStage::FAILED;
            report.error = error;
            report.completedAt = Timestamp::now();
            report_ = report;
            return false;
        }
        report.bridgeProcessRunning = true;
    }

    BridgeClientConfig config;
    config.host = "127.0.0.1";
    config.port = 8791;
    config.connectTimeoutMillis = 1000;
    config.readTimeoutMillis = 2000;
    bridgeClient_ = makePythonBridgeClient(config);

    report.stage = StartupStage::WAITING_FOR_HANDSHAKE;
    const bool handshakeOk = waitForHandshake(report);
    report.handshakeOk = handshakeOk;
    report.completedAt = Timestamp::now();
    report_ = report;

    if (!handshakeOk) {
        report.stage = StartupStage::DEGRADED;
        return false;
    }
    report.stage = StartupStage::READY;
    report.ready = true;
    report_ = report;
    return true;
}

bool StartupCoordinator::waitForHandshake(StartupReport& report) {
    if (!bridgeClient_) return false;
    const int attempts = options_.handshakeTimeoutMillis /
                         (options_.handshakePollMillis > 0
                              ? options_.handshakePollMillis
                              : 250);
    for (int i = 0; i <= attempts; ++i) {
        auto handshake = bridgeClient_->handshake();
        if (handshake.ok) {
            report.resolvedSymbol = handshake.value.resolvedSymbol;
            auto health = bridgeClient_->health();
            if (health.ok) {
                report.mt5Ready = health.value.mt5Ready;
            }
            if (!report.error.empty()) report.error.clear();
            return true;
        }
        report.error = handshake.error.message;
        report.detail = "handshake attempt " + std::to_string(i + 1);
        std::this_thread::sleep_for(
            std::chrono::milliseconds(options_.handshakePollMillis));
    }
    return false;
}

bool StartupCoordinator::recover(StartupReport& report) {
    if (!supervisor_) {
        report.error = "no bridge process to recover";
        return false;
    }
    std::string error;
    if (!supervisor_->restart(error)) {
        report.stage = StartupStage::FAILED;
        report.error = error;
        return false;
    }
    report.stage = StartupStage::WAITING_FOR_HANDSHAKE;
    if (!waitForHandshake(report)) {
        report.stage = StartupStage::DEGRADED;
        return false;
    }
    report.stage = StartupStage::READY;
    report.ready = true;
    return true;
}

void StartupCoordinator::shutdown() {
    if (supervisor_) {
        std::string error;
        supervisor_->stop(3000, error);
    }
    bridgeClient_.reset();
    supervisor_.reset();
}

}  // namespace aura
