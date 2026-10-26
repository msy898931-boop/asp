// ASTRA Desktop — offscreen snapshot tool.
//
// Renders the real shell (same startup sequence as main.cpp: same QSS, same
// fonts, same ThemeManager init) and saves PNG grabs of every page. All
// pixels come from the running Qt widget tree — nothing is fabricated.
//
// Usage:
//   astra_snapshot <output-directory>
//
// Produces, under <output-directory>:
//   dashboard_dark.png      dashboard_light.png
//   chart_dark.png          history_dark.png
//   health_dark.png         settings_dark.png
//   coming_soon_dark.png    fullscreen_dark.png
//
// The bare offscreen QPA platform reports an 800x800 screen, so normal pages
// are resized to 1440x900 and grabbed with QWidget::grab() (independent of the
// screen size). Fullscreen is exercised by posting a real F11 key event to
// MainWindow. Under a real 1440x900 X server (Xvfb + xcb) the fullscreen grab
// is exactly 1440x900; under bare offscreen it degrades to 800x800 and a note
// is printed.

#include "MainWindow.h"
#include "api/ApiClient.h"
#include "theme/ThemeManager.h"
#include <QApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QFileInfo>
#include <QFont>
#include <QImageWriter>
#include <QKeyEvent>
#include <QLabel>
#include <QMetaObject>
#include <QScrollArea>
#include <QScrollBar>
#include <QDebug>
#include <QPixmap>
#include <QScreen>
#include <QDir>
#include <QFrame>
#include <QSettings>
#include <algorithm>

#include <cstdio>

using astra::MainWindow;

namespace {

void emitKey(MainWindow* win, int key, Qt::KeyboardModifiers mods = Qt::NoModifier) {
    QKeyEvent press(QEvent::KeyPress, key, mods, QString());
    QKeyEvent release(QEvent::KeyRelease, key, mods, QString());
    QApplication::sendEvent(win, &press);
    QApplication::sendEvent(win, &release);
}

// Single ThemeManager shared by setup and every theme switch, so the
// application palette and QSS always stay in sync.
using astra::ThemeManager;

ThemeManager* themeManager() {
    static ThemeManager* mgr = new ThemeManager();
    return mgr;
}

// Applies a theme through ThemeManager so BOTH the QSS and the application
// palette are updated (custom-painted widgets read palette()).
void applyTheme(QApplication& app, const char* themeName) {
    Q_UNUSED(app);
    const bool dark = QString::fromLatin1(themeName).contains(QLatin1String("dark"));
    themeManager()->setTheme(dark ? ThemeManager::Theme::Dark : ThemeManager::Theme::Light);
    qDebug() << "Applied theme" << themeName;
}

// Replicates the startup sequence of frontend/qt/src/main.cpp verbatim so the
// snapshots show exactly what the shipped app shows.
void setupApp(QApplication& app) {
    QFont font = QApplication::font();
    font.setFamily("Inter");
    font.setPointSize(14);
    QApplication::setFont(font);

    themeManager()->setTheme(ThemeManager::Theme::Dark);
    qDebug() << "Loaded dark theme QSS";
}

void settle(int ms) {
    QApplication::processEvents();
    QElapsedTimer t; t.start();
    while (t.elapsed() < ms)
        QApplication::processEvents(QEventLoop::AllEvents, 50);
}

bool writePng(const QString& path, const QPixmap& px) {
    QImageWriter writer(path, "png");
    writer.setOptimizedWrite(true);
    if (!writer.write(px.toImage())) {
        fprintf(stderr, "FAILED to write %s: %s\n",
                qPrintable(path), qPrintable(writer.errorString()));
        return false;
    }
    return true;
}

bool capture(MainWindow& win, const char* name, const QString& outDir,
             int w = 1440, int h = 900) {
    win.resize(w, h);
    win.show();
    settle(500);

    // Re-applying the stylesheet forces a full re-polish/repaint of every
    // widget; offscreen, a programmatic resize alone can leave children painted
    // at their pre-resize geometry.
    const QString qss = qApp->styleSheet();
    qApp->setStyleSheet(QString());
    qApp->setStyleSheet(qss);
    settle(200);

    // Render (not grab): render() issues a fresh full paint of the whole tree so
    // children resized by the sidebar collapse are painted at their new size
    // instead of being served from a stale backing store.
    QPixmap px(win.size());
    px.setDevicePixelRatio(win.devicePixelRatioF());
    px.fill(Qt::transparent);
    win.render(&px);
    const QString path = outDir + "/" + QLatin1String(name);
    if (!writePng(path, px)) return false;

    QFileInfo fi(path);
    const bool sidebarVisible = win.findChild<QWidget*>("astraSidebar") &&
                                win.findChild<QWidget*>("astraSidebar")->isVisible();
    printf("%-22s  %dx%d  %lld bytes  isFullScreen=%s  sidebarVisible=%s\n",
           name, px.width(), px.height(), (long long)fi.size(),
           win.isFullScreen() ? "true" : "false",
           sidebarVisible ? "true" : "false");
    fflush(stdout);
    return true;
}

// Drive a private MainWindow slot by meta-name. The indices are the sidebar's
// flat order (MainWindow::setupSidebar):
//   0 Dashboard, 1 Chart, 2 History, 3 Health, 4 SMC/ICT,
//   5 Research (coming soon), 6 Knowledge, 7 Approval Center, 8 Governance,
//   9 Incidents, 10 Configuration (Settings), 11 Recovery.
void nav(MainWindow& win, int index) {
    QMetaObject::invokeMethod(&win, "onNavClicked", Qt::DirectConnection,
                              Q_ARG(int, index));
    QApplication::processEvents();
}

// Dump the vertical geometry of the dashboard's scroll page so overflow can
// be measured precisely. Enabled with ASTRA_SHOT_DEBUG=1.
void debugDashboard(const MainWindow& win) {
    if (qgetenv("ASTRA_SHOT_DEBUG").isEmpty()) return;

    const QList<QWidget*> pages = win.findChildren<QWidget*>();
    for (QWidget* w : pages) {
        if (QScrollArea* sc = qobject_cast<QScrollArea*>(w)) {
            QWidget* inner = sc->widget();
            if (!inner) continue;
            const int vmax = sc->verticalScrollBar() ? sc->verticalScrollBar()->maximum()
                                                     : 0;
            printf("SCROLLAREA  vmax=%d  inner.bottom=%d  viewport.h=%d\n",
                   vmax, inner->geometry().bottom(), sc->viewport()->height());
        }
    }
    // Locate the Refresh action button and the first quick card's caption.
    QWidget* refr = win.findChild<QWidget*>("dashRefreshButton");
    QWidget* central = win.centralWidget();
    if (!central) central = const_cast<MainWindow*>(&win);
    if (refr) {
        QPoint topLeft = refr->mapTo(central, QPoint(0, 0));
        printf("ACTIONBAR Refresh    y=%d h=%d  (visible if y+h <= %d)\n",
               topLeft.y(), refr->height(), 867);
    }
    // Dump every QFrame in the scroll page in geometry order.
    QWidget* page = nullptr;
    for (QWidget* w : win.findChildren<QScrollArea*>()) {
        if (w->inherits("QScrollArea") && !w->inherits("QTableWidget")) {
            page = w;
            break;
        }
    }
    if (page) {
        QList<QWidget*> all = page->findChildren<QWidget*>();
        // include the page itself
        all.prepend(page);
        QList<QWidget*> cands;
        for (QWidget* w : all) {
            if (qobject_cast<QFrame*>(w) && w->y() > 0 && w->height() > 10)
                cands.append(w);
        }
        std::sort(cands.begin(), cands.end(),
                  [](QWidget* a, QWidget* b) { return a->y() < b->y(); });
        for (QWidget* w : cands) {
            const QString name = w->objectName().isEmpty()
                ? (w->property("astraCard").toBool() ? QString("(card)")
                        : QLatin1String(w->metaObject()->className()))
                : w->objectName();
            printf("FRAME %-24s y=%4d h=%3d bottom=%4d visible=%d\n",
                   qPrintable(name), w->y(), w->height(),
                   w->y() + w->height(), w->isVisible());
        }
    }
    const QList<QLabel*> labels = win.findChildren<QLabel*>();
    for (const QLabel* l : labels) {
        if (l->text() == "Y" || l->text().isEmpty()) continue;
        const QString t = l->text().simplified();
        if (t.contains("RESEARCH", Qt::CaseInsensitive) && t.size() < 24) {
            QPoint topLeft = l->mapTo(central, QPoint(0, 0));
            printf("ROW4 caption '%s'  y=%d\n", qPrintable(t), topLeft.y());
            break;
        }
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    QApplication::setHighDpiScaleFactorRoundingPolicy(
        Qt::HighDpiScaleFactorRoundingPolicy::PassThrough);

    QApplication app(argc, argv);
    setupApp(app);

    QString outDir = argc > 1 ? QString::fromLocal8Bit(argv[1]) : QString(".");
    QDir().mkpath(outDir);

    // Mock API (same loopback base URL as the real app).
    astra::ApiClient apiClient;
    apiClient.setBaseUrl("http://127.0.0.1:8790/api/v1/");

    MainWindow win;
    win.setApiClient(&apiClient);
    win.resize(1440, 900);
    win.show();

    // Let the first polls from the mock API arrive.
    settle(1500);

    bool ok = true;

    // ── dashboard, dark ────────────────────────────────────────────────────
    nav(win, 0);
    win.initStyleChrome();
    ok &= capture(win, "dashboard_dark.png", outDir);
    debugDashboard(win);

    // ── dashboard, light (direct QSS apply) ───────────────────────────────
    applyTheme(app, "theme-light.qss");
    win.initStyleChrome();
    settle(600);
    ok &= capture(win, "dashboard_light.png", outDir);

    // Back to dark.
    applyTheme(app, "theme-dark.qss");
    win.initStyleChrome();
    settle(600);

    // ── chart page ─────────────────────────────────────────────────────────
    nav(win, 1);
    ok &= capture(win, "chart_dark.png", outDir);

    // ── history ────────────────────────────────────────────────────────────
    nav(win, 2);
    ok &= capture(win, "history_dark.png", outDir);

    // ── health ─────────────────────────────────────────────────────────────
    nav(win, 3);
    ok &= capture(win, "health_dark.png", outDir);

    // ── smc / ict (nav index 4) ────────────────────────────────────────────
    nav(win, 4);
    ok &= capture(win, "smc_dark.png", outDir);

    // ── settings (Configuration, nav index 10) ─────────────────────────────
    nav(win, 10);
    ok &= capture(win, "settings_dark.png", outDir);

    // ── coming soon (Research, nav index 5 -> shared ComingSoonPage) ───────
    nav(win, 5);
    ok &= capture(win, "coming_soon_dark.png", outDir);

    // ── light-theme pass over the remaining pages ──────────────────────────
    applyTheme(app, "theme-light.qss");
    win.initStyleChrome();
    settle(400);
    nav(win, 1);
    ok &= capture(win, "chart_light.png", outDir);
    nav(win, 2);
    ok &= capture(win, "history_light.png", outDir);
    nav(win, 3);
    ok &= capture(win, "health_light.png", outDir);
    nav(win, 4);
    ok &= capture(win, "smc_light.png", outDir);
    nav(win, 10);
    ok &= capture(win, "settings_light.png", outDir);
    nav(win, 5);
    ok &= capture(win, "coming_soon_light.png", outDir);
    applyTheme(app, "theme-dark.qss");
    win.initStyleChrome();
    settle(300);

    // ── sidebar collapsed (icons-only rail), dark + light ────────────────
    // Captured from a window built with the collapsed state already persisted:
    // this is what a user sees after toggling once and restarting (headless
    // offscreen cannot repaint a runtime width change).
    {
        QSettings st("ASTRA", "Desktop");
        st.setValue("sidebarCollapsed", true);
        st.sync();

        MainWindow rail;
        rail.setApiClient(&apiClient);
        rail.resize(1440, 900);
        rail.show();
        settle(1100);

        applyTheme(app, "theme-dark.qss");
        rail.initStyleChrome();
        settle(400);
        ok &= capture(rail, "sidebar_collapsed_dark.png", outDir);

        applyTheme(app, "theme-light.qss");
        rail.initStyleChrome();
        settle(400);
        ok &= capture(rail, "sidebar_collapsed_light.png", outDir);

        st.setValue("sidebarCollapsed", false);
        st.sync();
    }

    // Restore dark before the fullscreen grab.
    win.initStyleChrome();
    settle(300);

    // ── fullscreen, dark (real F11 key event) ─────────────────────────────
    nav(win, 0);
    emitKey(&win, Qt::Key_F11);
    QApplication::processEvents();

    const QScreen* screen = QApplication::primaryScreen();
    if (screen && screen->geometry().width() >= 1440) {
        // Real 1440x900 X server: the fullscreen grab matches the screen.
        settle(300);
        QPixmap px = win.grab();
        const QString path = outDir + "/fullscreen_dark.png";
        ok &= writePng(path, px);
        if (ok) {
            QFileInfo fi(path);
            const bool sidebarVisible = win.findChild<QWidget*>("astraSidebar") &&
                                        win.findChild<QWidget*>("astraSidebar")->isVisible();
            printf("%-22s  %dx%d  %lld bytes  isFullScreen=%s  sidebarVisible=%s\n",
                   "fullscreen_dark.png", px.width(), px.height(),
                   (long long)fi.size(),
                   win.isFullScreen() ? "true" : "false",
                   sidebarVisible ? "true" : "false");
            fflush(stdout);
        }
    } else {
        // There is no public resize API for the offscreen screen, so this
        // platform can only produce an 800x800 fullscreen grab. It is still
        // a genuine fullscreen grab (isFullScreen true, sidebar visible), but
        // not 1440x900 — run under Xvfb+xcb for the exact spec size.
        settle(300);
        QPixmap px = win.grab();
        const QString path = outDir + "/fullscreen_dark.png";
        ok &= writePng(path, px);
        if (ok) {
            QFileInfo fi(path);
            const bool sidebarVisible = win.findChild<QWidget*>("astraSidebar") &&
                                        win.findChild<QWidget*>("astraSidebar")->isVisible();
            printf("%-22s  %dx%d  %lld bytes  isFullScreen=%s  sidebarVisible=%s   "
                   "(note: offscreen screen caps fullscreen at 800x800; "
                   "run under Xvfb+xcb for 1440x900)\n",
                   "fullscreen_dark.png", px.width(), px.height(),
                   (long long)fi.size(),
                   win.isFullScreen() ? "true" : "false",
                   sidebarVisible ? "true" : "false");
            fflush(stdout);
        }
    }
    emitKey(&win, Qt::Key_Escape);
    QApplication::processEvents();

    return ok ? 0 : 1;
}