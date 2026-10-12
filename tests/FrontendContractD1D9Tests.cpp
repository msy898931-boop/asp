// TST-0021 - Frontend contract D1..D9 (transport + previously unexposed data).
//
// Closes the discrepancies recorded in project-control/FRONTEND_INTEGRATION_MAP.md:
// D1 loopback transport, D2 timeframe order/quality, D3 freshness/capability
// impact, D4 decision fields, D5 per-decision risk proposal, D6 append-only
// audit stream, D7 research/governance history, D8 shadow exit detail,
// D9 bridge identity.

#include "TestHarness.h"

#include "api/BackendFacade.h"
#include "api/LoopbackApiServer.h"
#include "foundation/HttpClient.h"
#include "governance/ApprovalGate.h"
#include "governance/IncidentManager.h"
#include "health/HealthMonitor.h"
#include "ledger/PredictionLedger.h"
#include "runtime/AuraRuntime.h"
#include "shadow/PositionSimulator.h"
#include "telegram/TelegramGateway.h"

#include <string>
#include <thread>

using namespace aura;

namespace {

struct Fixture {
    AuraRuntime runtime;
    HealthMonitor health;
    PredictionLedger ledger;
    PositionSimulator positions;
    IncidentManager incidents;
    ApprovalGate approvals;
    TelegramGateway telegram;
    BackendFacade facade;

    Fixture()
        : runtime(StartupOptions{}, 100),
          approvals(nullptr),
          facade(FacadeDependencies{&runtime, &health, &ledger, &positions,
                                    &incidents, &approvals, &telegram}) {}
};

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

void populateRuntime(AuraRuntime& runtime) {
    for (Timeframe tf : allTimeframes()) {
        const std::int64_t open = intervalMillis(tf) / 1000;
        const Bar bar = test::makeBar(tf, open, 2000.0, 2005.0, 1995.0, 2001.0);
        runtime.timeframeStore().updateFromClosedBar(bar, 1);
    }
}

}  // namespace

// D1: a loopback-only transport exposes the facade over HTTP.
TEST_CASE(d1_loopback_transport_serves_facade_contract) {
    Fixture fixture;
    populateRuntime(fixture.runtime);

    LoopbackApiServerConfig config;
    config.host = "127.0.0.1";
    config.port = 0;   // ephemeral
    LoopbackApiServer server(&fixture.facade, config);
    std::string error;
    REQUIRE(server.start(error));
    REQUIRE(server.isRunning());
    REQUIRE(server.isLoopbackOnly());

    HttpRequest request;
    request.host = "127.0.0.1";
    request.port = server.port();
    request.path = "/api/v1/system/state";
    const HttpResponse response = httpGet(request);
    REQUIRE(response.ok);
    CHECK_EQ(response.statusCode, 200);
    CHECK(contains(response.body, "\"api\":\"v1\""));
    CHECK(contains(response.body, "\"shadow_only\":true"));

    // A read route with the wrong method is rejected by the facade (405).
    HttpRequest post;
    post.host = "127.0.0.1";
    post.port = server.port();
    post.path = "/api/v1/system/state";
    post.method = "POST";
    post.body = "{}";
    const HttpResponse bad = httpRequest(post);
    REQUIRE(bad.ok);
    CHECK_EQ(bad.statusCode, 405);

    server.stop();
    CHECK(!server.isRunning());
}

// D1: the transport refuses any non-loopback bind.
TEST_CASE(d1_transport_refuses_non_loopback_bind) {
    Fixture fixture;
    LoopbackApiServerConfig config;
    config.host = "0.0.0.0";
    config.port = 0;
    LoopbackApiServer server(&fixture.facade, config);
    std::string error;
    CHECK(!server.start(error));
    CHECK(contains(error, "non-loopback"));
}

// D1: the command contract is carried over the transport, allow-list intact.
TEST_CASE(d1_command_route_enforces_allow_list) {
    Fixture fixture;
    LoopbackApiServerConfig config;
    config.port = 0;
    LoopbackApiServer server(&fixture.facade, config);
    std::string error;
    REQUIRE(server.start(error));

    HttpRequest denied;
    denied.host = "127.0.0.1";
    denied.port = server.port();
    denied.path = "/api/v1/command";
    denied.method = "POST";
    denied.body = "{\"command\":\"live.execute\",\"actor\":\"op\"}";
    const HttpResponse deniedResponse = httpRequest(denied);
    REQUIRE(deniedResponse.ok);
    CHECK_EQ(deniedResponse.statusCode, 403);

    HttpRequest notify;
    notify.host = "127.0.0.1";
    notify.port = server.port();
    notify.path = "/api/v1/command";
    notify.method = "POST";
    notify.body = "{\"command\":\"notify\",\"actor\":\"op\",\"payload\":\"hi\"}";
    const HttpResponse accepted = httpRequest(notify);
    REQUIRE(accepted.ok);
    CHECK_EQ(accepted.statusCode, 202);
    CHECK(contains(accepted.body, "\"accepted\":true"));

    server.stop();
}

// D2/D3: timeframes are canonical-ordered with freshness + capability impact.
TEST_CASE(d2_d3_timeframes_expose_freshness_and_impact) {
    Fixture fixture;
    populateRuntime(fixture.runtime);
    fixture.runtime.timeframeStore().refreshFreshness(Timeframe::M15,
                                                      Timestamp::now());

    const ApiResponse response = fixture.facade.timeframes();
    CHECK_EQ(response.status, 200);
    // Canonical order: M15 appears before H4, which appears before MN1.
    const std::size_t m15 = response.body.find("\"timeframe\":\"M15\"");
    const std::size_t h4 = response.body.find("\"timeframe\":\"H4\"");
    const std::size_t mn1 = response.body.find("\"timeframe\":\"MN1\"");
    CHECK(m15 != std::string::npos);
    CHECK(h4 != std::string::npos);
    CHECK(mn1 != std::string::npos);
    CHECK(m15 < h4);
    CHECK(h4 < mn1);
    CHECK(contains(response.body, "\"freshness\":{"));
    CHECK(contains(response.body, "\"last_successful_update\":"));
    CHECK(contains(response.body, "\"capability_impact\":["));
    CHECK(contains(response.body, "\"impact\":"));
}

// D3: an unobserved timeframe reports UNKNOWN, never FRESH.
TEST_CASE(d3_unobserved_timeframe_is_never_fresh) {
    Fixture fixture;
    const ApiResponse response = fixture.facade.timeframes();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"observed\":false"));
    CHECK(contains(response.body, "\"freshness\":null"));
    CHECK(contains(response.body, "\"quality\":{\"state\":\"UNKNOWN\""));
}

// D4: the decision contract exposes states + version identity.
TEST_CASE(d4_latest_signal_exposes_decision_states) {
    Fixture fixture;
    PredictionRecord record;
    record.decisionId = EntityId("M15-9000-LONG");
    record.timeframe = Timeframe::M15;
    record.asOfBarOpenSec = 9000;
    record.direction = SignalDirection::LONG;
    record.score = 71.0;
    record.confidence = 0.6;
    record.recordedAt = Timestamp::fromEpochMillis(9000000);
    REQUIRE(fixture.ledger.append(record));

    const ApiResponse response = fixture.facade.latestSignal();
    CHECK_EQ(response.status, 200);
    // Without a live decision cycle, states are explicitly UNKNOWN (not guessed).
    CHECK(contains(response.body, "\"data_state\":\"UNKNOWN\""));
    CHECK(contains(response.body, "\"structure_state\":\"UNKNOWN\""));
    CHECK(contains(response.body, "\"regime_state\":\"UNKNOWN\""));
    CHECK(contains(response.body, "\"eligibility_state\":\"UNKNOWN\""));
    CHECK(contains(response.body, "\"strategy_version\":null"));
    CHECK(contains(response.body, "\"configuration_version\":null"));
    // The live price is exposed beside the decision's own reference, and with
    // no tick observed it is explicitly null rather than copied from the bar.
    CHECK(contains(response.body, "\"last_price\":null"));
    CHECK(contains(response.body, "\"last_price_time\":null"));
}

// D5: risk route always reports portfolio risk, proposal explicitly gated.
TEST_CASE(d5_latest_risk_reports_proposal_availability) {
    Fixture fixture;
    const ApiResponse response = fixture.facade.latestRisk();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"proposal_available\":false"));
    CHECK(contains(response.body, "\"proposal\":null"));
    CHECK(contains(response.body, "\"risk_bounded_by_guardian\":true"));
}

// D6: audit route exposes the append-only stream.
TEST_CASE(d6_audit_route_exposes_append_only_stream) {
    Fixture fixture;
    AuditRecord record;
    record.eventId = EntityId("AUD-TEST-1");
    record.action = AuditAction::DECISION_PRODUCED;
    record.outcome = AuditOutcome::SUCCESS;
    record.serviceState = ServiceState::ONLINE;
    record.occurredAt = Timestamp::fromEpochMillis(1000);
    record.actor = "test";
    record.subject = "M15-1-LONG";
    record.details = "unit test";
    REQUIRE(fixture.runtime.audit().append(record));

    const ApiResponse response = fixture.facade.recentAudit();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"audit_records\":["));
    CHECK(contains(response.body, "\"action\":\"DECISION_PRODUCED\""));
    CHECK(contains(response.body, "\"record_hash\":"));
    CHECK(contains(response.body, "\"audit_stream_size\":1"));
}

// D7: research status exposes experiment + failure history arrays.
TEST_CASE(d7_research_status_exposes_history) {
    Fixture fixture;
    const ApiResponse response = fixture.facade.researchStatus();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"experiments\":["));
    CHECK(contains(response.body, "\"failures\":["));
    CHECK(contains(response.body, "\"experiment_count\":0"));
    CHECK(contains(response.body,
                   "research output never grants execution authority"));
}

// D7: governance exposes full request history, still never authorising live.
TEST_CASE(d7_governance_exposes_history_and_never_authorises_live) {
    Fixture fixture;
    const ApiResponse response = fixture.facade.governanceStatus();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"history\":["));
    CHECK(contains(response.body, "\"live_trading_authorised\":false"));
}

// D8: a closed shadow position exposes exit price, P&L and timestamps.
TEST_CASE(d8_shadow_position_exposes_exit_detail) {
    Fixture fixture;
    ShadowCommand command;
    command.commandId = EntityId("SC-1");
    command.decisionId = EntityId("M15-1-LONG");
    command.direction = SignalDirection::LONG;
    command.entryPrice = 2000.0;
    command.stopPrice = 1990.0;
    command.targetPrice = 2020.0;
    command.requestedLots = 0.1;
    command.approvedRiskFraction = 0.01;
    command.timeframe = Timeframe::M15;
    REQUIRE(fixture.positions.open(command, 0, Timestamp::fromEpochMillis(1000)));
    const Bar exitBar =
        test::makeBar(Timeframe::M15, 900, 2000.0, 2025.0, 1999.0, 2024.0);
    fixture.positions.advance(exitBar);

    const ApiResponse response = fixture.facade.shadowPositions();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"exit_price\":"));
    CHECK(contains(response.body, "\"realized_pnl\":"));
    CHECK(contains(response.body, "\"opened_at\":"));
    CHECK(contains(response.body, "\"closed_at\":"));
    CHECK(contains(response.body, "\"close_reason\":"));
    CHECK(contains(response.body, "\"shadow_only\":true"));
}

// D9: bridge status is exposed, and is explicit when unobserved.
TEST_CASE(d9_bridge_status_is_explicit_when_unobserved) {
    Fixture fixture;
    const ApiResponse response =
        fixture.facade.handle("GET", "/api/v1/bridge/status");
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"transport\":\"http_loopback\""));
    CHECK(contains(response.body, "\"loopback_only\":true"));
    CHECK(contains(response.body, "\"requires_manual_cmd\":false"));
    CHECK(contains(response.body, "\"observed\":false"));
    CHECK(contains(response.body, "\"broker\":null"));
}

// D9: bridge status never claims a broker when the bridge is unavailable.
TEST_CASE(d9_bridge_status_never_fabricates_broker) {
    Fixture fixture;
    const ApiResponse response = fixture.facade.bridgeStatus();
    CHECK_EQ(response.status, 200);
    CHECK(contains(response.body, "\"mt5_ready_live\":false"));
    CHECK(contains(response.body, "\"last_successful_request\":null"));
}
