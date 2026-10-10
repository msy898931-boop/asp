// HOST-0011 - AURA backend host entry point.
//
// This is the double-clickable backend process. It resolves its own paths,
// starts the bundled Python MT5 bridge as a supervised child, and runs the
// runtime loop. The user is never asked to open a terminal or run Python.

#include "api/BackendFacade.h"
#include "api/LoopbackApiServer.h"
#include "api/ProbabilityApi.h"
#include "governance/ApprovalGate.h"
#include "governance/IncidentManager.h"
#include "platform/windows/PathResolver.h"
#include "platform/windows/ProcessSupervisor.h"
#include "runtime/AuraRuntime.h"
#include "telegram/TelegramGateway.h"

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

namespace {

volatile std::sig_atomic_t g_stopRequested = 0;

void handleSignal(int) { g_stopRequested = 1; }

int parseApiPort(int argc, char** argv) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--api-port") {
            try {
                const int port = std::stoi(argv[i + 1]);
                if (port > 0 && port < 65536) return port;
            } catch (...) {
            }
        }
    }
    return 8790;   // frontend-facing loopback API (bridge uses 8791)
}

int parseIntervalMillis(int argc, char** argv) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--interval-ms") {
            try {
                return std::max(1000, std::stoi(argv[i + 1]));
            } catch (...) {
            }
        }
    }
    return 15000;   // M15 cadence by default
}

bool wantsDevFallback(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--dev-system-python") return true;
    }
    return false;
}

// Market-data source the bridge should serve. Explicit flag wins over the
// environment so a launcher (or a compose file) can set ASTRA_DATA_SOURCE
// while an operator can still override it on the command line.
std::string parseDataSource(int argc, char** argv) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--data-source") {
            return std::string(argv[i + 1]);
        }
    }
    if (const char* env = std::getenv("ASTRA_DATA_SOURCE")) {
        if (*env) return std::string(env);
    }
    return "mt5";
}

bool wantsOneShot(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--once") return true;
    }
    return false;
}

// Path to the durable calibration audit that opens the RULE C probability gate.
// Defaults to <appRoot>/AUDIT_REPORTS/AUDIT-T11-calibration.md (relocatable
// alongside the executable); overridable for tests and deployments.
std::string parseCalibrationAuditPath(int argc, char** argv,
                                      const std::string& appRootDir) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--calibration-audit") {
            return std::string(argv[i + 1]);
        }
    }
    return appRootDir + "/AUDIT_REPORTS/AUDIT-T11-calibration.md";
}

}  // namespace

int main(int argc, char** argv) {
    std::signal(SIGINT, handleSignal);
    std::signal(SIGTERM, handleSignal);

    std::cout << "AURA backend host starting\n";

    aura::AppPaths paths;
    std::string pathError;
    aura::PathResolver resolver;
    if (!resolver.resolve(paths, pathError)) {
        std::cerr << "FATAL: " << pathError << "\n";
        return 2;
    }
    std::cout << "  app root: " << paths.appRootDir << "\n";

    aura::StartupOptions options;
    options.allowSystemPythonFallback = wantsDevFallback(argc, argv);
    options.dataSource = parseDataSource(argc, argv);
    aura::AuraRuntime runtime(options);

    std::string startupError;
    const bool started = runtime.start(startupError);
    const aura::StartupReport& startup = runtime.startupReport();
    std::cout << "  startup stage: " << aura::toString(startup.stage) << "\n";
    if (!started) {
        std::cerr << "  startup not ready: " << startupError << "\n";
        // Continue in DEGRADED mode: the backend stays diagnosable.
    } else {
        std::cout << "  resolved symbol: " << startup.resolvedSymbol << "\n";
    }

    const int intervalMillis = parseIntervalMillis(argc, argv);
    const bool oneShot = wantsOneShot(argc, argv);

    // The in-process facade is the backend API surface the frontend talks to.
    // It is published to the ASTRA frontend through the loopback HTTP transport
    // started below (see PKG-0007).
    aura::IncidentManager incidents;
    aura::ApprovalGate approvals(runtime.guardian());
    aura::TelegramGateway telegram;

    // RULE C gate (audit C-1): "audited" is bound to the durable calibration
    // audit artifact, not an in-process toggle. In this build the T11 report is
    // PASS-on-methodology but explicitly NOT publication-authorised (synthetic
    // data, blocker E05), so the gate stays closed and the surface reports a
    // score — never a market probability.
    aura::ProbabilityApi probability;
    const aura::ProbabilityApi::Audit audit = probability.applyCalibrationAudit(
        parseCalibrationAuditPath(argc, argv, paths.appRootDir));
    std::cout << "  calibration audit: "
              << (audit.present ? "present" : "absent")
              << ", passed=" << (audit.passed ? "yes" : "no")
              << ", publication_authorised="
              << (audit.publicationAuthorised ? "yes" : "no") << " ("
              << audit.reason << ")\n";

    aura::AnalysisApi analysis;
    aura::BackendFacade facade(aura::FacadeDependencies{
        &runtime, &runtime.health(), &runtime.ledger(), &runtime.positions(),
        &incidents, &approvals, &telegram, &probability, &analysis});

    // The frontend-facing transport: a loopback-only HTTP/JSON server over the
    // facade. It is started by the host so the ASTRA frontend connects to a
    // running backend without the user starting anything manually. A bind
    // failure is reported but never fatal: the runtime keeps running.
    aura::LoopbackApiServerConfig apiConfig;
    apiConfig.host = "127.0.0.1";
    apiConfig.port = static_cast<std::uint16_t>(parseApiPort(argc, argv));
    aura::LoopbackApiServer apiServer(&facade, apiConfig);
    std::string apiError;
    if (apiServer.start(apiError)) {
        std::cout << "  frontend api: http://" << apiServer.bindAddress() << ":"
                  << apiServer.port() << "/api/v1\n";
    } else {
        std::cerr << "  frontend api unavailable: " << apiError << "\n";
    }

    // Development/ops surface: expose the current state summary once at start.
    const aura::ApiResponse systemState = facade.handle("GET", "/api/v1/system/state");
    std::cout << "  api system/state: " << systemState.body << "\n";

    // Telegram bot sidecar: launched only when a bot token is present. It reads
    // the loopback API started above and is supervised exactly like the MT5
    // bridge. Absence of a token is not an error — the backend runs without it.
    std::unique_ptr<aura::ProcessSupervisor> telegramBot;
    {
        const char* token = std::getenv("TELEGRAM_BOT_TOKEN");
        const std::string botScript =
            startup.paths.resourceDir + "/bridge/telegram_bot/astra_bot.py";
        if (token != nullptr && *token != '\0' && !startup.bundle.pythonExecutable.empty()) {
            aura::ProcessLaunchSpec botSpec;
            botSpec.executable = startup.bundle.pythonExecutable;
            botSpec.args = {botScript,
                            "--api", "http://127.0.0.1:" +
                                         std::to_string(apiServer.port()) + "/api/v1"};
            botSpec.workingDir = startup.paths.resourceDir + "/bridge/telegram_bot";
            botSpec.environment["PYTHONUNBUFFERED"] = "1";
            telegramBot = std::make_unique<aura::ProcessSupervisor>(botSpec);
            std::string botError;
            if (telegramBot->start(botError)) {
                std::cout << "  telegram bot: started (pid " << telegramBot->pid() << ")\n";
            } else {
                std::cerr << "  telegram bot: not started: " << botError << "\n";
                telegramBot.reset();
            }
        } else {
            std::cout << "  telegram bot: disabled (no TELEGRAM_BOT_TOKEN)\n";
        }
    }

    do {
        aura::RuntimeCycleReport cycle = runtime.tick(aura::Timestamp::now());
        std::cout << "cycle: ingested=" << cycle.timeframesIngested
                  << " failed=" << cycle.timeframesFailed
                  << " shadow=" << cycle.shadowCommandsIssued
                  << " mode=" << aura::toString(runtime.mode()) << "\n";
        for (const auto& issue : cycle.issues) {
            std::cerr << "  issue: " << issue << "\n";
        }
        if (oneShot) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(intervalMillis));
    } while (!g_stopRequested);

    std::cout << "AURA backend host shutting down\n";
    if (telegramBot) {
        std::string botError;
        telegramBot->stop(3000, botError);
    }
    apiServer.stop();
    runtime.stop();
    return started ? 0 : 1;
}
