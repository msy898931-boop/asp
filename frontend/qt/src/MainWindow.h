#pragma once
#include "api/ApiClient.h"
#include "theme/ThemeManager.h"
#include <QMainWindow>
#include <QWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QStackedWidget>
#include <QLabel>
#include <QTimer>
#include <QSettings>
#include <QVector>

class QPushButton;
class QCloseEvent;
class QEvent;
class QKeyEvent;
class QGraphicsOpacityEffect;
class QPropertyAnimation;
class QVariantAnimation;
class QSequentialAnimationGroup;

class DashboardPage;
class ChartPage;
class HistoryPage;
class HealthPage;
class SettingsPage;
class ComingSoonPage;
class SmcPage;

namespace astra {

class NavButton;
class SvgIcon;

// ──────────────────────────────────────────────────────────────────────────────
// MainWindow — ASTRA shell
// Layout: sidebar (232px, grouped navigation) + main area
//         (top bar 56px + content + bottom bar 32px)
// ──────────────────────────────────────────────────────────────────────────────

class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override = default;

    void setApiClient(ApiClient* client);
    void showExitConfirmation();
    // Applies the shell's inline styles from the current palette. Call once the
    // window is shown (after setStyleSheet propagation has settled).
    void initStyleChrome();

    // Sidebar collapse state (200px expanded <-> 64px icons-only). Exposed for
    // tests and the offscreen snapshot tool. animate=false applies instantly.
    [[nodiscard]] bool isSidebarCollapsed() const { return mSidebarCollapsed; }
    void setSidebarCollapsed(bool collapsed, bool animate);

protected:
    void keyPressEvent(QKeyEvent* event) override;
    void closeEvent(QCloseEvent* event) override;
    void changeEvent(QEvent* event) override;

private slots:
    void onNavClicked(int navIndex);
    void onRefreshClicked();
    void onThemeToggled();
    void onFullscreenToggled();
    void onSidebarToggled();
    void onCloseClicked();
    void onExitClicked();
    void onAnalysisUpdated(const AnalysisResponse& resp);
    void onHealthUpdated(const HealthResponse& resp);
    void onHistoryUpdated(const QVector<AnalysisData>& items);
    void onOffline();
    void onOnline();

private:
    void setupSidebar();
    void setupTopBar();
    void setupBottomBar();
    void setupContentArea();
    void updateLivenessIndicator();
    void updateBottomBarStatus();
    void updateClock();
    void applyTheme();
    // Re-applies the shell's inline styles from the current application
    // palette, so the sidebar/top bar/bottom bar follow a theme switch.
    void restyleChrome();
    void toggleFullscreen(bool enter);
    void showFullscreenHint();
    void hideFullscreenHint();
    void animateSidebar(bool collapsed, bool animate);
    void saveSettings();
    void stopAllTimers();
    [[nodiscard]] int navIndexOfPage(int pageIndex) const;

    // Navigation
    enum Page { Dashboard, Chart, History, Health, Smc, Settings, ComingSoon, PageCount };
    QStackedWidget* mContentStack = nullptr;
    QVector<QWidget*> mPages;

    // Sidebar — grouped navigation (label, target page or -1 = coming soon,
    // icon resource)
    struct NavEntry {
        QString label;
        int pageIndex;
        QString iconPath;
    };
    struct NavGroup {
        QString title;
        QVector<NavEntry> entries;
    };
    QVector<NavGroup> mNavGroups;
    QVector<int> mNavEntryPage;      // nav index -> page index (-1 coming soon)
    QVector<QString> mNavEntryLabel; // nav index -> display label
    QVector<NavButton*> mNavButtons;
    NavButton* mExitButton = nullptr;
    QWidget* mSidebar = nullptr;
    QLabel* mLogoLabel = nullptr;
    QLabel* mSubtitleLabel = nullptr;

    // Collapsible sidebar
    static constexpr int SIDEBAR_EXPANDED = 200;
    static constexpr int SIDEBAR_COLLAPSED = 64;
    QPushButton* mSidebarToggleBtn = nullptr;
    SvgIcon* mSidebarToggleIcon = nullptr;
    bool mSidebarCollapsed = false;
    QSequentialAnimationGroup* mSidebarGroup = nullptr;
    // Every faded text label (nav labels, group headers, lockup). One shared
    // QGraphicsOpacityEffect per label — Qt allows only one effect per widget.
    QVector<QGraphicsOpacityEffect*> mTextEffects;
    // Subset hidden entirely when collapsed (group headers + lockup), so they
    // cannot intercept clicks in the 64px rail.
    QVector<QGraphicsOpacityEffect*> mCollapseHiddenEffects;

    // Top bar
    QWidget* mTopBar = nullptr;
    QLabel* mPageTitle = nullptr;
    QLabel* mHealthDot = nullptr;
    QLabel* mHealthLabel = nullptr;
    QLabel* mClockLabel = nullptr;
    QTimer mClockTimer;
    QPushButton* mFullscreenBtn = nullptr;
    SvgIcon* mFullscreenIcon = nullptr;
    QPushButton* mThemeBtn = nullptr;
    QPushButton* mCloseBtn = nullptr;

    // Bottom bar
    QWidget* mBottomBar = nullptr;
    QLabel* mBackendStatus = nullptr;   // ● Runtime
    QLabel* mBridgeStatus = nullptr;    // ● Streams
    QLabel* mFreshnessStatus = nullptr; // ● Persistence
    QLabel* mDisclaimer = nullptr;      // System Health (right)

    // Theme
    ThemeManager* mThemeManager = nullptr;
    // True once setupSidebar/TopBar/BottomBar have built the shell widgets;
    // restyleChrome() must not run before then (PaletteChange fires during
    // construction when the application palette is first applied).
    bool mChromeReady = false;
    bool mIsFullscreen = false;
    QTimer mFullscreenHintTimer;
    bool mFullscreenHintVisible = false;

    // Polling
    QTimer mAnalysisPollTimer;
    QTimer mHealthPollTimer;
    ApiClient* mApiClient = nullptr;

    // Settings
    QSettings mSettings;

    // Current page index
    int mCurrentPage = 0;

    // Exit confirmation state
    bool mExitConfirmed = false;
};

}  // namespace astra
