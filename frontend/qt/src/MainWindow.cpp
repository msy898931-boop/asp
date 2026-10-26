#include "MainWindow.h"
#include "pages/DashboardPage.h"
#include "pages/ChartPage.h"
#include "pages/HistoryPage.h"
#include "pages/HealthPage.h"
#include "pages/SmcPage.h"
#include "pages/SettingsPage.h"
#include "pages/ComingSoonPage.h"
#include "dialogs/ConfirmExitDialog.h"
#include "widgets/NavButton.h"
#include "widgets/SvgIcon.h"
#include <QApplication>
#include <QKeyEvent>
#include <QCloseEvent>
#include <QEvent>
#include <QMessageBox>
#include <QFile>
#include <QTimer>
#include <QToolTip>
#include <QDateTime>
#include <QFrame>
#include <QSpacerItem>
#include <QGraphicsOpacityEffect>
#include <QPropertyAnimation>
#include <QVariantAnimation>
#include <QParallelAnimationGroup>
#include <QSequentialAnimationGroup>
#include <QAbstractAnimation>
#include <QButtonGroup>
#include <QPixmap>

namespace astra {

// Collapsible-sidebar durations (ms). All <= 250ms, per the animation rule.
static constexpr int SIDEBAR_ANIM_MS = 220;   // width transition
static constexpr int TEXT_FADE_MS = 120;      // collapse: text fades first
static constexpr int TEXT_FADE_IN_DELAY = 180; // expand: width, then fade in
static constexpr int TEXT_FADE_IN_MS = 120;

static constexpr int TOP_BAR_HEIGHT = 56;
static constexpr int BOTTOM_BAR_HEIGHT = 32;
static constexpr int MIN_WIDTH = 1366;
static constexpr int MIN_HEIGHT = 768;
static constexpr int DEFAULT_WIDTH = 1440;
static constexpr int DEFAULT_HEIGHT = 900;

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
    , mSettings("ASTRA", "Desktop")
    , mApiClient(nullptr)
{
    setWindowTitle("ASTRA Desktop");
    setMinimumSize(MIN_WIDTH, MIN_HEIGHT);
    resize(DEFAULT_WIDTH, DEFAULT_HEIGHT);

    // Restore geometry
    restoreGeometry(mSettings.value("windowGeometry").toByteArray());
    restoreState(mSettings.value("windowState").toByteArray());

    // Theme
    mThemeManager = new ThemeManager(this);
    bool darkTheme = mSettings.value("themeDark", true).toBool();
    mThemeManager->setTheme(darkTheme ? ThemeManager::Theme::Dark : ThemeManager::Theme::Light);
    // Custom-painted widgets (NavButton, chart placeholder) read this flag.
    qApp->setProperty("astraDark", darkTheme);

    // Layout
    QWidget* central = new QWidget(this);
    QVBoxLayout* mainLayout = new QVBoxLayout(central);
    mainLayout->setContentsMargins(0, 0, 0, 0);
    mainLayout->setSpacing(0);
    setCentralWidget(central);

    setupSidebar();
    setupTopBar();
    setupBottomBar();
    setupContentArea();
    mChromeReady = true;
    restyleChrome();

    // Restore the persisted sidebar state (no animation on startup).
    setSidebarCollapsed(mSettings.value("sidebarCollapsed", false).toBool(), false);

    // Fullscreen hint
    connect(&mFullscreenHintTimer, &QTimer::timeout, this, &MainWindow::hideFullscreenHint);

    // Header clock — HH:MM:SS UTC, ticks every second
    mClockTimer.setInterval(1000);
    connect(&mClockTimer, &QTimer::timeout, this, &MainWindow::updateClock);
    updateClock();
    mClockTimer.start();

    // Polling timers
    mAnalysisPollTimer.setInterval(5000);  // 5s per spec
    mHealthPollTimer.setInterval(10000);    // 10s per spec

    connect(&mAnalysisPollTimer, &QTimer::timeout, this, [this]() {
        if (mApiClient) mApiClient->fetchAnalysisLatest();
    });
    connect(&mHealthPollTimer, &QTimer::timeout, this, [this]() {
        if (mApiClient) mApiClient->fetchHealth();
    });

    // Start health polling immediately
    mHealthPollTimer.start();
}

void MainWindow::setApiClient(ApiClient* client) {
    mApiClient = client;
    connect(client, &ApiClient::analysisReceived, this, &MainWindow::onAnalysisUpdated);
    connect(client, &ApiClient::healthReceived, this, &MainWindow::onHealthUpdated);
    connect(client, &ApiClient::historyReceived, this, &MainWindow::onHistoryUpdated);
    connect(client, &ApiClient::offline, this, &MainWindow::onOffline);
    connect(client, &ApiClient::online, this, &MainWindow::onOnline);

    // Pages own their data sources
    if (auto* dash = qobject_cast<DashboardPage*>(mPages[Dashboard])) dash->setApiClient(client);
    if (auto* chart = qobject_cast<ChartPage*>(mPages[Chart])) chart->setApiClient(client);
    if (auto* hist = qobject_cast<HistoryPage*>(mPages[History])) hist->setApiClient(client);
    if (auto* hp = qobject_cast<HealthPage*>(mPages[Health])) hp->setApiClient(client);
    if (auto* sp = qobject_cast<SettingsPage*>(mPages[Settings])) sp->setApiClient(client);

    // Initial fetches
    client->fetchAnalysisLatest();
    client->fetchHealth();
    client->fetchAnalysisHistory(20);
    mAnalysisPollTimer.start();
}

void MainWindow::setupSidebar() {
    // Grouped navigation model:
    //   pageIndex >= 0 -> real page; -1 -> shared ComingSoonPage
    mNavGroups = {
        { "MONITORING", {
            { "Dashboard", Dashboard, ":/icons/dashboard.svg" },
            { "Chart",     Chart,     ":/icons/chart.svg" },
            { "History",   History,   ":/icons/history.svg" },
            { "Health",    Health,    ":/icons/health.svg" },
        }},
        { "INTELLIGENCE", {
            { "SMC / ICT", Smc, ":/icons/smc.svg" },
            { "Research", -1, "" },
            { "Knowledge", -1, "" },
        }},
        { "GOVERNANCE", {
            { "Approval Center", -1, "" },
            { "Governance",      -1, "" },
            { "Incidents",       -1, "" },
        }},
        { "SYSTEM", {
            { "Configuration", Settings, ":/icons/settings.svg" },
            { "Recovery",      -1,       "" },
        }},
    };

    mNavEntryPage.clear();
    mNavEntryLabel.clear();
    mNavButtons.clear();

    mSidebar = new QWidget(this);
    mSidebar->setObjectName("astraSidebar");  // stable handle for tests
    mSidebar->setFixedWidth(SIDEBAR_EXPANDED);
    QVBoxLayout* sidebarLayout = new QVBoxLayout(mSidebar);
    sidebarLayout->setContentsMargins(0, 0, 0, 0);
    sidebarLayout->setSpacing(0);

    // ── ASTRA lockup + subtitle ──
    QWidget* logoArea = new QWidget(mSidebar);
    logoArea->setFixedHeight(72);
    QVBoxLayout* logoLayout = new QVBoxLayout(logoArea);
    logoLayout->setContentsMargins(20, 16, 20, 10);
    logoLayout->setSpacing(2);

    mLogoLabel = new QLabel(logoArea);
    mLogoLabel->setText("ASTRA");
    mLogoLabel->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
    logoLayout->addWidget(mLogoLabel);

    mSubtitleLabel = new QLabel(logoArea);
    mSubtitleLabel->setText("XAUUSD Intelligence");
    mSubtitleLabel->setObjectName("astraSubtitle");
    mSubtitleLabel->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
    logoLayout->addWidget(mSubtitleLabel);

    // Divider below lockup
    QFrame* divider = new QFrame(mSidebar);
    divider->setObjectName("astraDivider");
    divider->setFixedHeight(1);

    // ── Groups ──
    QWidget* navArea = new QWidget(mSidebar);
    QVBoxLayout* navLayout = new QVBoxLayout(navArea);
    navLayout->setContentsMargins(12, 8, 12, 12);
    navLayout->setSpacing(4);

    for (const NavGroup& group : mNavGroups) {
        QLabel* groupLabel = new QLabel(navArea);
        groupLabel->setText(group.title.toUpper());
        groupLabel->setObjectName("astraGroupHeader");
        navLayout->addWidget(groupLabel);

        for (const NavEntry& entry : group.entries) {
            const bool comingSoon = entry.pageIndex < 0;
            NavButton* btn = new NavButton(entry.label, entry.iconPath, comingSoon, navArea);
            if (comingSoon) {
                btn->setToolTip("Coming soon — enabled when the backend module ships.");
            }
            const int navIndex = mNavButtons.size();
            mNavEntryPage.append(entry.pageIndex);
            mNavEntryLabel.append(entry.label);
            mNavButtons.append(btn);
            navLayout->addWidget(btn);
            connect(btn, &NavButton::clicked, this, [this, navIndex]() {
                onNavClicked(navIndex);
            });
        }
    }

    // Spacer pushes Exit to the bottom
    navLayout->addStretch();

    // ── Exit ──
    mExitButton = new NavButton("Exit", ":/icons/exit.svg", false, navArea);
    mExitButton->setObjectName("astraExitButton");  // stable handle for tests
    connect(mExitButton, &NavButton::clicked, this, &MainWindow::onExitClicked);
    navLayout->addWidget(mExitButton);

    sidebarLayout->addWidget(logoArea);
    sidebarLayout->addWidget(divider);
    sidebarLayout->addWidget(navArea);

    // ── Text labels that fade with the collapse ──
    // NavButton paints its label itself, so it gets a plain qreal property;
    // the QLabel-based text (lockup, subtitle, group headers) gets a single
    // QGraphicsOpacityEffect each (Qt allows one effect per widget).
    QVector<QWidget*> labelWrappers;
    labelWrappers << mLogoLabel << mSubtitleLabel;
    for (QLabel* l : mSidebar->findChildren<QLabel*>("astraGroupHeader")) {
        labelWrappers << l;
    }

    mTextEffects.clear();
    mCollapseHiddenEffects.clear();
    for (QWidget* w : labelWrappers) {
        auto* eff = new QGraphicsOpacityEffect(w);
        eff->setOpacity(1.0);
        w->setGraphicsEffect(eff);
        mTextEffects << eff;
        // Group headers and the wordmark carry no information when the rail is
        // 64px wide, so they are hidden (not just transparent) when collapsed.
        if (w->objectName() == "astraGroupHeader" || w == mLogoLabel || w == mSubtitleLabel) {
            mCollapseHiddenEffects << eff;
        }
    }

    // 40x40 flat toggle button (hamburger painted with QPainter so no icon
    // font is needed). Lives in the top bar, before the page title.
    mSidebarToggleBtn = new QPushButton();
    mSidebarToggleBtn->setObjectName("astraSidebarToggle");
    mSidebarToggleBtn->setFixedSize(40, 40);
    mSidebarToggleBtn->setCursor(Qt::PointingHandCursor);
    mSidebarToggleBtn->setToolTip("Toggle sidebar (Ctrl+B)");
    mSidebarToggleIcon = new SvgIcon(":/icons/menu.svg", 20, mSidebarToggleBtn);
    mSidebarToggleIcon->setObjectName("menuIcon");
    mSidebarToggleIcon->setAttribute(Qt::WA_TransparentForMouseEvents);
    mSidebarToggleIcon->move(10, 10);
    connect(mSidebarToggleBtn, &QPushButton::clicked, this, &MainWindow::onSidebarToggled);
}

void MainWindow::setupTopBar() {
    mTopBar = new QWidget(this);
    mTopBar->setFixedHeight(TOP_BAR_HEIGHT);

    // Outer column owns the widget: [ content row, 1px bottom border ].
    QVBoxLayout* topBarInner = new QVBoxLayout(mTopBar);
    topBarInner->setContentsMargins(0, 0, 0, 0);
    topBarInner->setSpacing(0);

    QHBoxLayout* topLayout = new QHBoxLayout();
    topBarInner->addLayout(topLayout);
    topLayout->setContentsMargins(20, 0, 20, 0);
    topLayout->setSpacing(12);

    // Left: sidebar toggle, then page title
    topLayout->addWidget(mSidebarToggleBtn);
    mPageTitle = new QLabel(mTopBar);
    mPageTitle->setObjectName("pageTitle");  // stable handle for tests
    mPageTitle->setText("Dashboard");
    topLayout->addWidget(mPageTitle);

    QSpacerItem* titleSpacer = new QSpacerItem(20, 20, QSizePolicy::Expanding, QSizePolicy::Minimum);
    topLayout->addSpacerItem(titleSpacer);

    // Right: system health chip (● SYSTEM HEALTHY | DEGRADED | OFFLINE)
    QWidget* healthChip = new QWidget(mTopBar);
    QHBoxLayout* healthLayout = new QHBoxLayout(healthChip);
    healthLayout->setContentsMargins(0, 0, 0, 0);
    healthLayout->setSpacing(6);
    mHealthDot = new QLabel(healthChip);
    mHealthDot->setFixedSize(8, 8);
    mHealthDot->setStyleSheet("QLabel { background: #4CAF7A; border-radius: 4px; }");
    mHealthLabel = new QLabel(healthChip);
    mHealthLabel->setText("SYSTEM HEALTHY");
    mHealthLabel->setStyleSheet(
        "QLabel { color: #4CAF7A; font-size: 11px; font-weight: 500; letter-spacing: 0.05em; }"
    );
    healthLayout->addWidget(mHealthDot, 0, Qt::AlignVCenter);
    healthLayout->addWidget(mHealthLabel);
    topLayout->addWidget(healthChip);

    // ● SHADOW ONLY — the lock emoji (U+1F512) has no glyph in the target
    // fonts (renders as a tofu box), so use the same filled dot as the bottom
    // bar. Keeps SHADOW ONLY double-labelled in top and bottom bars.
    QLabel* shadowChip = new QLabel(mTopBar);
    shadowChip->setObjectName("astraShadowChip");
    shadowChip->setText(QStringLiteral("\u25CF SHADOW ONLY"));
    topLayout->addWidget(shadowChip);

    // Renderer
    QLabel* rendererLabel = new QLabel(mTopBar);
    rendererLabel->setObjectName("astraRendererLabel");
    rendererLabel->setText("Renderer: Qt6/QPainter");
    topLayout->addWidget(rendererLabel);

    // Clock — HH:MM:SS UTC (updates every second)
    mClockLabel = new QLabel(mTopBar);
    mClockLabel->setObjectName("astraClockLabel");
    topLayout->addWidget(mClockLabel);

    // Theme toggle
    mThemeBtn = new QPushButton(mTopBar);
    mThemeBtn->setObjectName("astraThemeBtn");
    mThemeBtn->setFixedSize(32, 32);
    mThemeBtn->setToolTip("Toggle theme (Ctrl+T)");
    mThemeBtn->setText(QStringLiteral("\u263D"));  // moon initially
    connect(mThemeBtn, &QPushButton::clicked, this, &MainWindow::onThemeToggled);

    // Fullscreen toggle — the ⛶ glyph (U+26F6) has no font coverage on the
    // target platform (tofu box), so render the existing fullscreen.svg icon
    // tinted to the active theme instead.
    mFullscreenBtn = new QPushButton(mTopBar);
    mFullscreenBtn->setObjectName("astraFullscreenBtn");
    mFullscreenBtn->setFixedSize(32, 32);
    mFullscreenBtn->setToolTip("Toggle fullscreen (F11)");
    mFullscreenIcon = new SvgIcon(":/icons/fullscreen.svg", 16, mFullscreenBtn);
    mFullscreenIcon->setAttribute(Qt::WA_TransparentForMouseEvents);
    mFullscreenIcon->move(8, 8);
    connect(mFullscreenBtn, &QPushButton::clicked, this, &MainWindow::onFullscreenToggled);

    // Close (X)
    mCloseBtn = new QPushButton(mTopBar);
    mCloseBtn->setObjectName("astraCloseBtn");
    mCloseBtn->setFixedSize(32, 32);
    mCloseBtn->setToolTip("Close");
    mCloseBtn->setText("X");
    connect(mCloseBtn, &QPushButton::clicked, this, &MainWindow::onCloseClicked);

    topLayout->addWidget(mThemeBtn);
    topLayout->addWidget(mFullscreenBtn);
    topLayout->addWidget(mCloseBtn);

    // Bottom border line — stretches with the bar
    QFrame* bottomLine = new QFrame(mTopBar);
    bottomLine->setObjectName("astraTopBorder");
    bottomLine->setFixedHeight(1);
    topBarInner->addWidget(bottomLine);
}

void MainWindow::setupBottomBar() {
    mBottomBar = new QWidget(this);
    mBottomBar->setFixedHeight(BOTTOM_BAR_HEIGHT);

    // Outer column owns the widget: [ 1px top border, content row ].
    QVBoxLayout* bbInner = new QVBoxLayout(mBottomBar);
    bbInner->setContentsMargins(0, 0, 0, 0);
    bbInner->setSpacing(0);

    QFrame* topLine = new QFrame(mBottomBar);
    topLine->setObjectName("astraBottomBorder");
    topLine->setFixedHeight(1);
    bbInner->addWidget(topLine);

    QHBoxLayout* bottomLayout = new QHBoxLayout();
    bbInner->addLayout(bottomLayout);
    bottomLayout->setContentsMargins(20, 0, 20, 0);
    bottomLayout->setSpacing(24);

    // Left: ● Runtime | ● Streams | ● Persistence | ● Recovery
    mBackendStatus = new QLabel(mBottomBar);
    mBridgeStatus = new QLabel(mBottomBar);
    mFreshnessStatus = new QLabel(mBottomBar);
    QLabel* recovery = new QLabel(mBottomBar);

    mBackendStatus->setObjectName("runtimeLabel");
    mBridgeStatus->setObjectName("streamsLabel");
    mFreshnessStatus->setObjectName("persistenceLabel");
    recovery->setObjectName("recoveryLabel");

    mBackendStatus->setText(QStringLiteral("\u25CF Runtime: \u2014"));
    mBridgeStatus->setText(QStringLiteral("\u25CF Streams: \u2014"));
    mFreshnessStatus->setText(QStringLiteral("\u25CF Persistence: \u2014"));
    recovery->setText(QStringLiteral("\u25CF Recovery: \u2014"));

    bottomLayout->addWidget(mBackendStatus);
    bottomLayout->addWidget(mBridgeStatus);
    bottomLayout->addWidget(mFreshnessStatus);
    bottomLayout->addWidget(recovery);

    QSpacerItem* spacer = new QSpacerItem(20, 20, QSizePolicy::Expanding, QSizePolicy::Minimum);
    bottomLayout->addSpacerItem(spacer);

    // Right: ● SHADOW ONLY | System Health
    QLabel* shadow = new QLabel(mBottomBar);
    shadow->setObjectName("astraShadowLabel");
    shadow->setText(QStringLiteral("\u25CF SHADOW ONLY"));
    bottomLayout->addWidget(shadow);

    mDisclaimer = new QLabel(mBottomBar);
    mDisclaimer->setObjectName("systemHealthLabel");
    mDisclaimer->setText("System Health: \u2014");
    bottomLayout->addWidget(mDisclaimer);

    updateBottomBarStatus();
}

void MainWindow::setupContentArea() {
    mContentStack = new QStackedWidget(this);

    // Create pages
    mPages.resize(PageCount);
    mPages[Dashboard] = new DashboardPage(this);
    mPages[Chart] = new ChartPage(this);       // stub — no /candles endpoint
    mPages[History] = new HistoryPage(this);
    mPages[Health] = new HealthPage(this);
    mPages[Smc] = new SmcPage(this);
    mPages[Settings] = new SettingsPage(this);
    mPages[ComingSoon] = new ComingSoonPage(this);

    for (int i = 0; i < PageCount; ++i) {
        mContentStack->addWidget(mPages[i]);
    }

    // Initial active nav row
    if (!mNavButtons.isEmpty()) {
        mNavButtons[0]->setActive(true);
    }

    // Layout: sidebar on left, top bar, content, bottom bar
    QVBoxLayout* centralLayout = qobject_cast<QVBoxLayout*>(centralWidget()->layout());
    QHBoxLayout* rowLayout = new QHBoxLayout();
    rowLayout->setContentsMargins(0, 0, 0, 0);
    rowLayout->setSpacing(0);
    rowLayout->addWidget(mSidebar);
    QVBoxLayout* rightColumn = new QVBoxLayout();
    rightColumn->setContentsMargins(0, 0, 0, 0);
    rightColumn->setSpacing(0);
    rightColumn->addWidget(mTopBar);
    rightColumn->addWidget(mContentStack);
    rightColumn->addWidget(mBottomBar);
    rightColumn->setStretch(1, 1);  // content expands
    rowLayout->addLayout(rightColumn);
    centralLayout->addLayout(rowLayout);
}

int MainWindow::navIndexOfPage(int pageIndex) const {
    for (int i = 0; i < mNavEntryPage.size(); ++i) {
        if (mNavEntryPage[i] == pageIndex) return i;
    }
    return -1;
}

void MainWindow::onNavClicked(int navIndex) {
    if (navIndex < 0 || navIndex >= mNavButtons.size()) return;
    if (mNavButtons[navIndex]->isActive()) return;  // already the active row

    // Active state across the whole sidebar (including coming-soon rows)
    for (NavButton* btn : mNavButtons) {
        btn->setActive(false);
    }
    mNavButtons[navIndex]->setActive(true);

    const int targetPage = mNavEntryPage[navIndex];
    if (targetPage >= 0) {
        mCurrentPage = targetPage;
        mContentStack->setCurrentIndex(targetPage);
    } else {
        // Shared "Coming soon" destination, titled after the clicked module
        auto* cs = qobject_cast<ComingSoonPage*>(mPages[ComingSoon]);
        if (cs) cs->setModuleName(mNavEntryLabel[navIndex]);
        mCurrentPage = ComingSoon;
        mContentStack->setCurrentIndex(ComingSoon);
    }
    mPageTitle->setText(mNavEntryLabel[navIndex]);

    // Polling per page
    if (mCurrentPage == Dashboard) {
        if (mApiClient) {
            mApiClient->fetchAnalysisLatest();
            mApiClient->fetchAnalysisHistory(20);
        }
        mAnalysisPollTimer.start();
    } else {
        mAnalysisPollTimer.stop();
    }

    if (mCurrentPage == History && mApiClient) {
        mApiClient->fetchAnalysisHistory(50);
    }

    if (mCurrentPage == Smc) {
        // The SMC engine is its own process on its own port; the page talks to
        // it directly and never through the backend host, so the frozen v1
        // contract is untouched.
        if (auto* smc = qobject_cast<SmcPage*>(mPages[Smc])) smc->refresh();
    }
}

void MainWindow::onRefreshClicked() {
    if (mApiClient) {
        mApiClient->fetchAnalysisLatest();
        mApiClient->fetchHealth();
        if (mCurrentPage == History) {
            mApiClient->fetchAnalysisHistory(50);
        } else {
            mApiClient->fetchAnalysisHistory(20);
        }
    }
}

void MainWindow::onThemeToggled() {
    ThemeManager::Theme newTheme =
        mThemeManager->currentTheme() == ThemeManager::Theme::Dark
            ? ThemeManager::Theme::Light
            : ThemeManager::Theme::Dark;
    mThemeManager->setTheme(newTheme);
    mSettings.setValue("themeDark", mThemeManager->isDark());
    // Update icon
    mThemeBtn->setText(mThemeManager->isDark() ? QStringLiteral("\u263D")
                                               : QStringLiteral("\u2600"));  // moon / sun
    applyTheme();
}

void MainWindow::onFullscreenToggled() {
    toggleFullscreen(!mIsFullscreen);
}

void MainWindow::onCloseClicked() {
    showExitConfirmation();
}

void MainWindow::onExitClicked() {
    showExitConfirmation();
}

void MainWindow::saveSettings() {
    mSettings.setValue("windowGeometry", saveGeometry());
    mSettings.setValue("windowState", saveState());
    mSettings.setValue("themeDark", mThemeManager->isDark());
}

void MainWindow::stopAllTimers() {
    mAnalysisPollTimer.stop();
    mHealthPollTimer.stop();
    mClockTimer.stop();
    mFullscreenHintTimer.stop();
    if (mApiClient) {
        mApiClient->cancelAll();
    }
}

void MainWindow::onAnalysisUpdated(const AnalysisResponse& /*resp*/) {
    // Keep the dashboard in sync regardless of the visible page
    if (auto* dash = qobject_cast<DashboardPage*>(mPages[Dashboard])) {
        dash->updateFromAnalysis(mApiClient->currentAnalysis());
    }
    updateLivenessIndicator();
    updateBottomBarStatus();
}

void MainWindow::onHealthUpdated(const HealthResponse& resp) {
    updateLivenessIndicator();
    updateBottomBarStatus();

    if (auto* dash = qobject_cast<DashboardPage*>(mPages[Dashboard])) {
        dash->updateFromHealth(resp);
    }
    if (auto* hp = qobject_cast<HealthPage*>(mPages[Health])) {
        hp->updateFromHealth(resp);
    }
}

void MainWindow::onHistoryUpdated(const QVector<AnalysisData>& items) {
    if (auto* dash = qobject_cast<DashboardPage*>(mPages[Dashboard])) {
        dash->updateFromHistory(items);
    }
    if (auto* hist = qobject_cast<HistoryPage*>(mPages[History])) {
        hist->updateFromHistory(items);
    }
}

void MainWindow::onOffline() {
    updateLivenessIndicator();
    updateBottomBarStatus();
    if (auto* dash = qobject_cast<DashboardPage*>(mPages[Dashboard])) {
        dash->setOnline(false);
    }
}

void MainWindow::onOnline() {
    updateLivenessIndicator();
    updateBottomBarStatus();
    if (auto* dash = qobject_cast<DashboardPage*>(mPages[Dashboard])) {
        dash->setOnline(true);
    }
}

void MainWindow::updateLivenessIndicator() {
    if (!mHealthLabel || !mHealthDot) return;
    QString state;
    QString color;

    if (!mApiClient || !mApiClient->isOnline()) {
        state = "SYSTEM OFFLINE";
        color = "#D95A5A";
    } else {
        const HealthData health = mApiClient->currentHealth().data;
        if (health.status == "offline" || !mApiClient->isOnline()) {
            state = "SYSTEM OFFLINE";
            color = "#D95A5A";
        } else if (health.status == "degraded" || mApiClient->isDegraded()) {
            state = "SYSTEM DEGRADED";
            color = "#D9A14A";
        } else {
            state = "SYSTEM HEALTHY";
            color = "#4CAF7A";
        }
    }

    mHealthLabel->setText(state);
    mHealthLabel->setStyleSheet(
        QString("QLabel { color: %1; font-size: 11px; font-weight: 500; letter-spacing: 0.05em; }")
            .arg(color));
    mHealthDot->setStyleSheet(
        QString("QLabel { background: %1; border-radius: 4px; }").arg(color));
}

void MainWindow::updateBottomBarStatus() {
    if (!mBackendStatus || !mBridgeStatus || !mFreshnessStatus || !mDisclaimer) return;
    const QString em = QStringLiteral("\u2014");
    const QString red = "#D95A5A";
    const QString amber = "#D9A14A";
    const QString green = "#4CAF7A";
    const QString grey = palette().color(QPalette::WindowText).name();
    const QString dim = palette().color(QPalette::PlaceholderText).name();

    auto styleFor = [](const QString& color) {
        return QString("QLabel { color: %1; font-size: 11px; letter-spacing: 0.03em; }").arg(color);
    };

    if (!mApiClient) {
        mBackendStatus->setText(QStringLiteral("\u25CF Runtime: ") + em);
        mBackendStatus->setStyleSheet(styleFor(grey));
        mBridgeStatus->setText(QStringLiteral("\u25CF Streams: ") + em);
        mBridgeStatus->setStyleSheet(styleFor(grey));
        mDisclaimer->setText("System Health: " + em);
        mDisclaimer->setStyleSheet(
            QString("QLabel { color: %1; font-size: 11px; }").arg(dim));
        return;
    }

    // Persistence / Recovery have no backend surface in the frozen contract
    mFreshnessStatus->setText(QStringLiteral("\u25CF Persistence: ") + em);
    mFreshnessStatus->setStyleSheet(styleFor(grey));

    if (!mApiClient->isOnline()) {
        mBackendStatus->setText(QStringLiteral("\u25CF Runtime: OFFLINE"));
        mBackendStatus->setStyleSheet(styleFor(red));
        mBridgeStatus->setText(QStringLiteral("\u25CF Streams: OFFLINE"));
        mBridgeStatus->setStyleSheet(styleFor(red));
        mDisclaimer->setText("System Health: OFFLINE");
        mDisclaimer->setStyleSheet(
            QString("QLabel { color: %1; font-size: 11px; }").arg(red));
        return;
    }

    const HealthData health = mApiClient->currentHealth().data;

    // ● Runtime — backend aggregate status
    if (health.status == "ok") {
        mBackendStatus->setText(QStringLiteral("\u25CF Runtime: ONLINE"));
        mBackendStatus->setStyleSheet(styleFor(green));
    } else if (health.status == "degraded") {
        mBackendStatus->setText(QStringLiteral("\u25CF Runtime: DEGRADED"));
        mBackendStatus->setStyleSheet(styleFor(amber));
    } else {
        mBackendStatus->setText(QStringLiteral("\u25CF Runtime: OFFLINE"));
        mBackendStatus->setStyleSheet(styleFor(red));
    }

    // ● Streams — bridge status
    if (health.bridge == "ok") {
        mBridgeStatus->setText(QStringLiteral("\u25CF Streams: OK"));
        mBridgeStatus->setStyleSheet(styleFor(green));
    } else if (health.bridge == "stale") {
        mBridgeStatus->setText(QStringLiteral("\u25CF Streams: STALE"));
        mBridgeStatus->setStyleSheet(styleFor(amber));
    } else {
        mBridgeStatus->setText(QStringLiteral("\u25CF Streams: OFFLINE"));
        mBridgeStatus->setStyleSheet(styleFor(red));
    }

    // Right side: System Health
    QString healthState;
    QString healthColor;
    if (health.status == "ok") {
        healthState = "OK";
        healthColor = green;
    } else if (health.status == "degraded") {
        healthState = "DEGRADED";
        healthColor = amber;
    } else {
        healthState = "OFFLINE";
        healthColor = red;
    }
    mDisclaimer->setText("System Health: " + healthState);
    mDisclaimer->setStyleSheet(
        QString("QLabel { color: %1; font-size: 11px; }").arg(healthColor));
}

void MainWindow::updateClock() {
    if (!mClockLabel) return;
    const QString now =
        QDateTime::currentDateTimeUtc().toString("HH:mm:ss") + QStringLiteral(" UTC");
    mClockLabel->setText(now);
}

void MainWindow::initStyleChrome() {
    restyleChrome();
}

void MainWindow::restyleChrome() {
    const QPalette pal = qApp->palette();
    const QString textPrimary = pal.color(QPalette::Text).name();
    const QString textSecondary = pal.color(QPalette::WindowText).name();
    const QString textMuted = pal.color(QPalette::PlaceholderText).name();
    const QString surface = pal.color(QPalette::Base).name();
    const QString surface2 = pal.color(QPalette::AlternateBase).name();
    const QString border = pal.color(QPalette::Mid).name();

    // Sidebar
    if (mLogoLabel) {
        mLogoLabel->setStyleSheet(
            QString("QLabel { color: %1; font-family: 'Inter', 'Segoe UI', system-ui, "
                    "sans-serif; font-size: 18px; font-weight: 600; letter-spacing: 4px; }")
                .arg(textPrimary));
    }
    const QList<QLabel*> subtitles = findChildren<QLabel*>("astraSubtitle");
    for (QLabel* l : subtitles) {
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 10px; "
                                 "letter-spacing: 0.08em; }").arg(textMuted));
    }
    const QList<QLabel*> groupLabels = findChildren<QLabel*>("astraGroupHeader");
    for (QLabel* l : groupLabels) {
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 10px; font-weight: 500; "
                                 "letter-spacing: 0.08em; padding: 12px 8px 4px 8px; }")
                             .arg(textMuted));
    }
    const QList<QFrame*> dividers = findChildren<QFrame*>("astraDivider");
    for (QFrame* f : dividers) f->setStyleSheet(QString("QFrame { background: %1; }").arg(border));

    // Top bar
    if (mPageTitle) {
        mPageTitle->setStyleSheet(
            QString("QLabel { color: %1; font-size: 22px; font-weight: 600; }")
                .arg(textPrimary));
    }
    const QList<QLabel*> shadowChips = findChildren<QLabel*>("astraShadowChip");
    for (QLabel* l : shadowChips) {
        l->setStyleSheet(
            QString("QLabel { color: %1; font-size: 12px; font-weight: 500; "
                    "letter-spacing: 0.05em; border: 1px solid %2; border-radius: 12px; "
                    "padding: 4px 12px; background: %3; }")
                .arg(textSecondary, border, surface));
    }
    const QList<QLabel*> rendererLabels = findChildren<QLabel*>("astraRendererLabel");
    for (QLabel* l : rendererLabels) {
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 11px; }").arg(textMuted));
    }
    const QList<QLabel*> clockLabels = findChildren<QLabel*>("astraClockLabel");
    for (QLabel* l : clockLabels) {
        l->setStyleSheet(
            QString("QLabel { color: %1; font-size: 12px; "
                    "font-family: 'JetBrains Mono', 'Consolas', monospace; }")
                .arg(textSecondary));
    }
    for (QPushButton* b : {mThemeBtn, mFullscreenBtn, mSidebarToggleBtn}) {
        if (b) {
            b->setStyleSheet(
                QString("QPushButton { background: transparent; border: none; padding: 4px; }"
                        "QPushButton:hover { background: %1; }").arg(surface2));
        }
    }
    if (mFullscreenIcon) mFullscreenIcon->setColor(textSecondary);
    if (mSidebarToggleIcon) mSidebarToggleIcon->setColor(textSecondary);
    if (mCloseBtn) {
        mCloseBtn->setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 4px; }"
            "QPushButton:hover { background: #D95A5A; color: white; }");
    }
    const QList<QFrame*> topBorders = findChildren<QFrame*>("astraTopBorder");
    for (QFrame* f : topBorders) f->setStyleSheet(QString("QFrame { background: %1; }").arg(border));

    // Bottom bar
    const QList<QFrame*> bottomBorders = findChildren<QFrame*>("astraBottomBorder");
    for (QFrame* f : bottomBorders) f->setStyleSheet(QString("QFrame { background: %1; }").arg(border));

    // Base color for the status labels; the semantic state colors are re-applied
    // by updateBottomBarStatus() below.
    const QString baseStyle =
        QString("QLabel { color: %1; font-size: 11px; letter-spacing: 0.03em; }")
            .arg(textSecondary);
    for (QLabel* l : {mBackendStatus, mBridgeStatus, mFreshnessStatus}) {
        if (l) l->setStyleSheet(baseStyle);
    }
    const QList<QLabel*> shadowLabels = findChildren<QLabel*>("astraShadowLabel");
    for (QLabel* l : shadowLabels) l->setStyleSheet(baseStyle);

    // Re-apply liveness + bottom-bar status, which layer semantic colors on top.
    updateLivenessIndicator();
    updateBottomBarStatus();
}

void MainWindow::applyTheme() {
    qApp->setProperty("astraDark", mThemeManager->isDark());
    qApp->setStyleSheet(mThemeManager->qssContent());
    restyleChrome();
    // Custom-painted widgets repaint with their theme-aware colors.
    for (NavButton* btn : mNavButtons) {
        btn->refreshThemeColors();
    }
    if (mExitButton) mExitButton->refreshThemeColors();
}

void MainWindow::onSidebarToggled() {
    setSidebarCollapsed(!mSidebarCollapsed, true);
}

void MainWindow::setSidebarCollapsed(bool collapsed, bool animate) {
    if (!mSidebar) return;
    animateSidebar(collapsed, animate);
}

void MainWindow::animateSidebar(bool collapsed, bool animate) {
    mSidebarCollapsed = collapsed;
    const int to = collapsed ? SIDEBAR_COLLAPSED : SIDEBAR_EXPANDED;

    // Stop any in-flight animation so rapid toggles cannot leave the rail at an
    // intermediate width.
    if (mSidebarGroup) mSidebarGroup->stop();

    // Shared text-opacity pools: QLabel-based text via one effect each, and
    // NavButton-painted labels via their textOpacity property.
    QVector<QObject*> textTargets;
    for (QGraphicsOpacityEffect* eff : mTextEffects) textTargets << eff;
    for (NavButton* btn : mNavButtons) textTargets << btn;
    if (mExitButton) textTargets << mExitButton;

    auto makeFadeGroup = [&](qreal from, qreal to_) {
        auto* g = new QParallelAnimationGroup;
        for (QObject* t : textTargets) {
            const char* prop = qobject_cast<QGraphicsOpacityEffect*>(t) ? "opacity"
                                                                        : "textOpacity";
            auto* a = new QPropertyAnimation(t, prop, g);
            a->setDuration(collapsed ? TEXT_FADE_MS : TEXT_FADE_IN_MS);
            a->setStartValue(from);
            a->setEndValue(to_);
            a->setEasingCurve(QEasingCurve::InOutCubic);
            g->addAnimation(a);
        }
        return g;
    };

    if (!animate) {
        mSidebar->setFixedWidth(to);
        // A width change clamps the widget but not its children's cached
        // backing store, so force the whole subtree to repaint at the new size.
        mSidebar->repaint();
        for (QWidget* c : mSidebar->findChildren<QWidget*>()) c->repaint();
        for (QGraphicsOpacityEffect* eff : mTextEffects) eff->setOpacity(collapsed ? 0.0 : 1.0);
        for (NavButton* btn : mNavButtons) btn->setTextOpacity(collapsed ? 0.0 : 1.0);
        if (mExitButton) mExitButton->setTextOpacity(collapsed ? 0.0 : 1.0);
        if (collapsed) {
            for (QGraphicsOpacityEffect* eff : mCollapseHiddenEffects) {
                if (QWidget* w = qobject_cast<QWidget*>(eff->parent())) w->setVisible(false);
            }
        }
        return;
    }

    // Animate the real width: the icon column (fixed x=12 in NavButton) stays
    // put while the label area shrinks away (220ms, InOutCubic).
    auto* widthAnim = new QPropertyAnimation(mSidebar, "minimumWidth");
    widthAnim->setDuration(SIDEBAR_ANIM_MS);
    widthAnim->setStartValue(mSidebar->width());
    widthAnim->setEndValue(to);
    widthAnim->setEasingCurve(QEasingCurve::InOutCubic);
    connect(widthAnim, &QPropertyAnimation::valueChanged, this,
            [this](const QVariant& v) {
                mSidebar->setFixedWidth(v.toInt());
            });

    // Persistent, single-owner group: it owns both the width and fade
    // animations and is deleted only when the next toggle starts.
    auto* group = new QSequentialAnimationGroup(this);
    if (collapsed) {
        // Fade text out first (0-120ms), pause, then shrink the width
        // (200-420ms) — the spec's Option A.
        for (QGraphicsOpacityEffect* eff : mTextEffects) eff->setOpacity(1.0);
        for (NavButton* btn : mNavButtons) btn->setTextOpacity(1.0);
        if (mExitButton) mExitButton->setTextOpacity(1.0);
        group->addAnimation(makeFadeGroup(1.0, 0.0));
        group->addPause(80);
        group->addAnimation(widthAnim);
    } else {
        // Grow the width first, then fade text in (180-300ms).
        for (QGraphicsOpacityEffect* eff : mTextEffects) {
            if (QWidget* w = qobject_cast<QWidget*>(eff->parent())) w->setVisible(true);
            eff->setOpacity(0.0);
        }
        for (NavButton* btn : mNavButtons) btn->setTextOpacity(0.0);
        if (mExitButton) mExitButton->setTextOpacity(0.0);
        group->addAnimation(widthAnim);
        group->addPause(TEXT_FADE_IN_DELAY - SIDEBAR_ANIM_MS);
        group->addAnimation(makeFadeGroup(0.0, 1.0));
    }
    connect(group, &QSequentialAnimationGroup::finished, this, [this, collapsed]() {
        // Settled: pin to the exact width (clips to 64 or relaxes to 200).
        mSidebar->setFixedWidth(collapsed ? SIDEBAR_COLLAPSED : SIDEBAR_EXPANDED);
        // Persist the settled state (never an intermediate width).
        mSettings.setValue("sidebarCollapsed", collapsed);
        mSettings.sync();
        // Collapsed: now that the rail is 64px, drop the information-free text
        // (group headers, wordmark) so it cannot intercept clicks.
        if (collapsed) {
            for (QGraphicsOpacityEffect* eff : mCollapseHiddenEffects) {
                if (QWidget* w = qobject_cast<QWidget*>(eff->parent())) w->setVisible(false);
            }
        }
    });
    // Replace the previous group (owns its animations) now that we have one.
    if (mSidebarGroup) {
        mSidebarGroup->stop();
        mSidebarGroup->deleteLater();
    }
    mSidebarGroup = group;
    group->start();
}

void MainWindow::toggleFullscreen(bool enter) {
    // Fullscreen removes only the OS window chrome. Sidebar, top bar and
    // bottom bar all stay visible — no child widget is ever hidden here.
    if (enter) {
        showFullScreen();
        mIsFullscreen = true;
        showFullscreenHint();
    } else {
        showNormal();
        mIsFullscreen = false;
        mFullscreenHintTimer.stop();
        mFullscreenHintVisible = false;
    }
}

void MainWindow::changeEvent(QEvent* event) {
    if (event->type() == QEvent::WindowStateChange) {
        // Keep mIsFullscreen truthful when the OS (Win+Shift+Arrows, taskbar)
        // changes the window state — and never hide child widgets.
        mIsFullscreen = isFullScreen();
        if (!mIsFullscreen) {
            mFullscreenHintTimer.stop();
            mFullscreenHintVisible = false;
        }
    } else if (event->type() == QEvent::PaletteChange) {
        // ThemeManager swaps the application palette per theme; the shell's
        // inline styles must be rebuilt to follow it.
        if (mChromeReady) restyleChrome();
    }
    QMainWindow::changeEvent(event);
}

void MainWindow::showFullscreenHint() {
    // Transient top-right hint; fades after 3 seconds
    if (mFullscreenHintVisible) return;
    mFullscreenHintVisible = true;

    QLabel* hint = new QLabel(this);
    hint->setText("Press F11 to exit fullscreen");
    const QPalette pal = qApp->palette();
    hint->setStyleSheet(
        QString("QLabel { background: %1; color: %2; font-size: 12px; "
                "padding: 6px 12px; border-radius: 6px; }")
            .arg(pal.color(QPalette::AlternateBase).name(),
                 pal.color(QPalette::WindowText).name())
    );
    hint->setAttribute(Qt::WA_TransparentForMouseEvents);
    hint->move(width() - 280, 12);
    hint->setVisible(true);

    mFullscreenHintTimer.start(3000);
    connect(&mFullscreenHintTimer, &QTimer::timeout, hint, [hint]() {
        hint->setVisible(false);
        hint->deleteLater();
    });
}

void MainWindow::hideFullscreenHint() {
    mFullscreenHintTimer.stop();
    mFullscreenHintVisible = false;
}

void MainWindow::keyPressEvent(QKeyEvent* event) {
    // Global shortcuts
    if (event->matches(QKeySequence::Close)) {
        showExitConfirmation();
        return;
    }

    const bool ctrlDown = (event->modifiers() & Qt::CTRL) != 0;
    if (ctrlDown && event->key() == Qt::Key_Q) {
        showExitConfirmation();
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_R) {
        onRefreshClicked();
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_T) {
        onThemeToggled();
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_B) {
        onSidebarToggled();
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_Comma) {
        onNavClicked(navIndexOfPage(Settings));
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_D) {
        onNavClicked(navIndexOfPage(Dashboard));
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_H) {
        onNavClicked(navIndexOfPage(Chart));
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_L) {
        onNavClicked(navIndexOfPage(History));
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_K) {
        onNavClicked(navIndexOfPage(Health));
        return;
    }

    if (ctrlDown && event->key() == Qt::Key_M) {
        onNavClicked(navIndexOfPage(Smc));
        return;
    }

    // F11 — fullscreen toggle
    if (event->key() == Qt::Key_F11) {
        toggleFullscreen(!mIsFullscreen);
        return;
    }

    // ESC — exit fullscreen
    if (event->key() == Qt::Key_Escape) {
        if (mIsFullscreen) {
            toggleFullscreen(false);
            return;
        }
    }

    // Chart page: 1-9 for timeframe
    if (mCurrentPage == Chart && event->key() >= Qt::Key_1 && event->key() <= Qt::Key_9) {
        auto* chart = qobject_cast<ChartPage*>(mPages[Chart]);
        if (chart) chart->onTimeframeKey(event->key() - Qt::Key_1);
    }

    // Chart page: +/- zoom
    if (mCurrentPage == Chart) {
        if (event->key() == Qt::Key_Plus || event->key() == Qt::Key_Equal) {
            auto* chart = qobject_cast<ChartPage*>(mPages[Chart]);
            if (chart) chart->zoomIn();
        } else if (event->key() == Qt::Key_Minus) {
            auto* chart = qobject_cast<ChartPage*>(mPages[Chart]);
            if (chart) chart->zoomOut();
        } else if (ctrlDown && event->key() == Qt::Key_0) {
            auto* chart = qobject_cast<ChartPage*>(mPages[Chart]);
            if (chart) chart->resetZoom();
        }
    }

    QMainWindow::keyPressEvent(event);
}

void MainWindow::closeEvent(QCloseEvent* event) {
    if (mExitConfirmed) {
        event->accept();
        return;
    }
    ConfirmExitDialog dlg(this);
    if (dlg.exec() == QDialog::Accepted) {
        mExitConfirmed = true;
        saveSettings();
        stopAllTimers();
        event->accept();
    } else {
        event->ignore();
    }
}

void MainWindow::showExitConfirmation() {
    if (mExitConfirmed) {
        close();
        return;
    }
    ConfirmExitDialog dlg(this);
    if (dlg.exec() == QDialog::Accepted) {
        mExitConfirmed = true;
        saveSettings();
        stopAllTimers();
        close();  // closeEvent() sees mExitConfirmed and accepts immediately
    }
}

}  // namespace astra
