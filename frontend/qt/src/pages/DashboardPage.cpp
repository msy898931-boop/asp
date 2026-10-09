#include "DashboardPage.h"
#include "theme/Theme.h"
#include "api/ApiClient.h"
#include <QApplication>
#include <QScrollArea>
#include <QHeaderView>
#include <QPainter>
#include <QPaintEvent>
#include <QButtonGroup>
#include <QFrame>
#include <QSpacerItem>
#include <QFont>
#include <QTimer>

namespace astra {

// ── palette (IMAGE 1: ASTRA deep navy + silver) ──────────────────────────────
static const char* kBg       = "#0A1628";
static const char* kSurface  = "#0F1F35";
static const char* kSurface2 = "#162A44";
static const char* kText     = "#E8EEF5";
static const char* kText2    = "#8FA3BF";
static const char* kText3    = "#5A6B80";
static const char* kAccent   = "#4A90D9";
static const char* kGreen    = "#4CAF7A";
static const char* kRed      = "#D95A5A";
static const char* kAmber    = "#D9A14A";
static const char* kEm       = "\u2014";  // em dash for unknown values

// ──────────────────────────────────────────────────────────────────────────────
// ChartPlaceholderWidget — subtle line-art background + honest "Coming soon".
// /api/v1/candles does not exist in the frozen contract, so no price line,
// no candles, no fabricated data — grid only.
// No QGraphicsView, no OpenGL: plain QPainter.
// ──────────────────────────────────────────────────────────────────────────────
class ChartPlaceholderWidget : public QWidget {
public:
    explicit ChartPlaceholderWidget(QWidget* parent = nullptr)
        : QWidget(parent)
    {
        setMinimumHeight(150);
        setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        const bool dark = qApp->property("astraDark").toBool();

        p.fillRect(rect(), QColor(dark ? kSurface : "#FFFFFF"));

        // Subtle line-art grid
        QPen grid(dark ? QColor(22, 42, 68, 160) : QColor(225, 230, 237, 220));
        grid.setWidthF(1.0);
        p.setPen(grid);
        for (int x = 0; x < width(); x += 48) p.drawLine(x, 0, x, height());
        for (int y = 0; y < height(); y += 40) p.drawLine(0, y, width(), y);

        // Card inner border
        p.setPen(QPen(QColor(dark ? kSurface2 : "#E1E6ED"), 1));
        p.setBrush(Qt::NoBrush);
        p.drawRect(rect().adjusted(0, 0, -1, -1));

        // Centered messages
        QFont f = font();
        f.setPixelSize(16);
        f.setWeight(QFont::DemiBold);
        p.setFont(f);
        p.setPen(QColor(dark ? kText2 : kText3));
        p.drawText(rect().adjusted(0, 0, 0, -16), Qt::AlignCenter,
                   QStringLiteral("Coming soon"));

        QFont sf = font();
        sf.setPixelSize(11);
        p.setFont(sf);
        p.setPen(astra::uiColor(kText3));
        p.drawText(rect().adjusted(12, 16, -12, 0), Qt::AlignCenter,
                   QStringLiteral("Chart data pending \u2014 /api/v1/candles is not "
                                  "part of the frozen API contract"));
    }
};

DashboardPage::DashboardPage(QWidget* parent)
    : QWidget(parent)
{
    setupLayout();
}

void DashboardPage::setApiClient(ApiClient* client) {
    mApiClient = client;
    if (mApiClient && mApiClient->isOnline()) {
        setOnline(true);
        updateFromHealth(mApiClient->currentHealth());
    }
}

// ── small builders ───────────────────────────────────────────────────────────

QFrame* DashboardPage::makeCard(QWidget* parent, const QString& title) {
    QFrame* card = new QFrame(parent);
    card->setProperty("astraCard", true);
    card->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* lay = new QVBoxLayout(card);
    lay->setContentsMargins(16, 14, 16, 14);
    lay->setSpacing(4);

    QLabel* caption = new QLabel(card);
    caption->setText(title);
    astra::setStyle(caption, 
        QString("QLabel { color: %1; font-size: 10px; font-weight: 500; "
                "letter-spacing: 0.08em; }").arg(kText2));
    lay->addWidget(caption);
    return card;
}

QLabel* DashboardPage::makeCardValue(QWidget* parent) {
    QLabel* value = new QLabel(parent);
    value->setText(kEm);
    astra::setStyle(value, 
        QString("QLabel { color: %1; font-size: 20px; font-weight: 600; "
                "font-family: 'JetBrains Mono', 'Consolas', monospace; }").arg(kText3));
    value->setWordWrap(true);
    return value;
}

QLabel* DashboardPage::makeCardSub(QWidget* parent) {
    QLabel* sub = new QLabel(parent);
    sub->setText(kEm);
    astra::setStyle(sub, 
        QString("QLabel { color: %1; font-size: 11px; }").arg(kText3));
    sub->setWordWrap(true);
    return sub;
}

void DashboardPage::setCardValue(QLabel* label, const QString& text, const QString& color) {
    label->setText(text);
    astra::setStyle(label, 
        QString("QLabel { color: %1; font-size: 20px; font-weight: 600; "
                "font-family: 'JetBrains Mono', 'Consolas', monospace; }").arg(color));
}

// ── layout ───────────────────────────────────────────────────────────────────

void DashboardPage::setupLayout() {
    // Scroll container so nothing clips at the 1366x768 minimum window.
    QVBoxLayout* outer = new QVBoxLayout(this);
    outer->setContentsMargins(0, 0, 0, 0);
    outer->setSpacing(0);

    QScrollArea* scroll = new QScrollArea(this);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setWidgetResizable(true);
    scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    astra::setStyle(scroll, "QScrollArea { background: transparent; }");
    outer->addWidget(scroll);

    QWidget* page = new QWidget(scroll);
    astra::setStyle(page, QString("QWidget { background: %1; }").arg(kBg));
    scroll->setWidget(page);

    QVBoxLayout* mainLayout = new QVBoxLayout(page);
    mainLayout->setContentsMargins(20, 14, 20, 14);
    mainLayout->setSpacing(12);

    // ─────────────── ROW 1 — status cards ───────────────
    QHBoxLayout* row1 = new QHBoxLayout();
    row1->setSpacing(12);

    QFrame* healthCard = makeCard(page, "SYSTEM HEALTH");
    mHealthValue = makeCardValue(healthCard);
    mHealthSub = makeCardSub(healthCard);
    healthCard->layout()->addWidget(mHealthValue);
    healthCard->layout()->addWidget(mHealthSub);
    row1->addWidget(healthCard, 1);

    QFrame* streamsCard = makeCard(page, "DATA STREAMS");
    mStreamsValue = makeCardValue(streamsCard);
    mStreamsSub = makeCardSub(streamsCard);
    streamsCard->layout()->addWidget(mStreamsValue);
    streamsCard->layout()->addWidget(mStreamsSub);
    row1->addWidget(streamsCard, 1);

    QFrame* signalsCard = makeCard(page, "SIGNALS");
    mSignalsValue = makeCardValue(signalsCard);
    mSignalsSub = makeCardSub(signalsCard);
    signalsCard->layout()->addWidget(mSignalsValue);
    signalsCard->layout()->addWidget(mSignalsSub);
    row1->addWidget(signalsCard, 1);

    QFrame* riskCard = makeCard(page, "RISK");
    mRiskValue = makeCardValue(riskCard);
    mRiskSub = makeCardSub(riskCard);
    setCardValue(mRiskValue, "UNKNOWN", kAmber);
    mRiskSub->setText("risk module pending");
    riskCard->layout()->addWidget(mRiskValue);
    riskCard->layout()->addWidget(mRiskSub);
    row1->addWidget(riskCard, 1);

    QFrame* execCard = makeCard(page, "EXECUTION");
    mExecutionValue = makeCardValue(execCard);
    mExecutionSub = makeCardSub(execCard);
    setCardValue(mExecutionValue, "SHADOW ONLY", kAccent);
    mExecutionSub->setText("mode");
    execCard->layout()->addWidget(mExecutionValue);
    execCard->layout()->addWidget(mExecutionSub);
    row1->addWidget(execCard, 1);

    mainLayout->addLayout(row1);

    // ─────────────── ROW 2 — chart (60%) + signals (40%) ───────────────
    QHBoxLayout* row2 = new QHBoxLayout();
    row2->setSpacing(14);
    row2->setStretch(0, 6);
    row2->setStretch(1, 4);

    // Chart card
    QFrame* chartCard = new QFrame(page);
    chartCard->setProperty("astraCard", true);
    chartCard->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* chartLay = new QVBoxLayout(chartCard);
    chartLay->setContentsMargins(16, 14, 16, 16);
    chartLay->setSpacing(10);

    QHBoxLayout* chartHeader = new QHBoxLayout();
    chartHeader->setSpacing(8);
    QLabel* chartTitle = new QLabel(chartCard);
    chartTitle->setText("XAUUSD");
    astra::setStyle(chartTitle, 
        QString("QLabel { color: %1; font-size: 15px; font-weight: 600; }").arg(kText));
    chartHeader->addWidget(chartTitle);

    // Timeframe tabs (visual only while /api/v1/candles is missing)
    QButtonGroup* tfGroup = new QButtonGroup(chartCard);
    tfGroup->setExclusive(true);
    const char* tfs[] = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"};
    for (int i = 0; i < 9; ++i) {
        QPushButton* tab = new QPushButton(QString::fromLatin1(tfs[i]), chartCard);
        tab->setCheckable(true);
        tab->setFixedHeight(24);
        tab->setCursor(Qt::PointingHandCursor);
        tab->setToolTip("Chart data pending — /api/v1/candles is not in the frozen API contract.");
        const QString baseStyle =
            QString("QPushButton { font-size: 11px; padding: 2px 9px; border-radius: 6px; "
                    "border: 1px solid %1; color: %2; background: transparent; min-width: 0; }")
                .arg(kSurface2, kText2);
        const QString activeStyle =
            QString("QPushButton { font-size: 11px; padding: 2px 9px; border-radius: 6px; "
                    "border: 1px solid %1; color: %2; background: %1; min-width: 0; }")
                .arg(kSurface2, kText);
        astra::setStyle(tab, baseStyle);
        tfGroup->addButton(tab, i);
        chartHeader->addWidget(tab);
        if (i == 2) tab->setChecked(true);  // M15 default (matches ChartPage)
        connect(tab, &QPushButton::toggled, this, [tab, baseStyle, activeStyle](bool on) {
            astra::setStyle(tab, on ? activeStyle : baseStyle);
        });
    }
    chartHeader->addStretch();
    chartLay->addLayout(chartHeader);

    chartLay->addWidget(new ChartPlaceholderWidget(chartCard));
    row2->addWidget(chartCard, 6);

    // Signals card — /analysis/history
    QFrame* histCard = new QFrame(page);
    histCard->setProperty("astraCard", true);
    histCard->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* histLay = new QVBoxLayout(histCard);
    histLay->setContentsMargins(16, 14, 16, 16);
    histLay->setSpacing(8);

    QLabel* histCaption = new QLabel(histCard);
    histCaption->setText("SIGNALS \u2014 /analysis/history?limit=20");
    astra::setStyle(histCaption, 
        QString("QLabel { color: %1; font-size: 10px; font-weight: 500; "
                "letter-spacing: 0.08em; }").arg(kText2));
    histLay->addWidget(histCaption);

    mSignalsStack = new QStackedWidget(histCard);
    mSignalsTable = new QTableWidget(mSignalsStack);
    mSignalsTable->setColumnCount(4);
    mSignalsTable->setHorizontalHeaderLabels({"Time", "Dir", "Score", "Tier"});
    mSignalsTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
    mSignalsTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    mSignalsTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    mSignalsTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::ResizeToContents);
    mSignalsTable->verticalHeader()->setVisible(false);
    mSignalsTable->verticalHeader()->setDefaultSectionSize(26);
    mSignalsTable->setSelectionMode(QAbstractItemView::NoSelection);
    mSignalsTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    mSignalsTable->setShowGrid(false);
    mSignalsTable->setFocusPolicy(Qt::NoFocus);
    astra::setStyle(mSignalsTable, 
        QString("QTableWidget { background: %1; border: 1px solid %2; "
                "border-radius: 8px; font-size: 12px; color: %3; }"
                "QTableWidget::item { border-bottom: 1px solid %2; }"
                "QHeaderView::section { background: %2; color: %4; border: none; "
                "padding: 6px 8px; font-size: 10px; font-weight: 500; "
                "letter-spacing: 0.05em; }")
            .arg(kSurface, kSurface2, kText, kText2));

    mSignalsEmpty = new QLabel(mSignalsStack);
    mSignalsEmpty->setText("No signals yet \u2014 waiting for\n/api/v1/analysis/history");
    mSignalsEmpty->setAlignment(Qt::AlignCenter);
    astra::setStyle(mSignalsEmpty, 
        QString("QLabel { color: %1; font-size: 12px; }").arg(kText3));

    mSignalsStack->addWidget(mSignalsEmpty);  // 0 = empty state
    mSignalsStack->addWidget(mSignalsTable);  // 1 = data
    histLay->addWidget(mSignalsStack);
    row2->addWidget(histCard, 4);

    mainLayout->addLayout(row2);

    // ─────────────── ROW 3 — timeframe matrix (60%) + risk panel (40%) ───
    QHBoxLayout* row3 = new QHBoxLayout();
    row3->setSpacing(14);
    row3->setStretch(0, 6);
    row3->setStretch(1, 4);

    QFrame* matrixCard = new QFrame(page);
    matrixCard->setProperty("astraCard", true);
    matrixCard->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* matrixLay = new QVBoxLayout(matrixCard);
    matrixLay->setContentsMargins(16, 14, 16, 16);
    matrixLay->setSpacing(8);

    QLabel* matrixCaption = new QLabel(matrixCard);
    matrixCaption->setText("TIMEFRAME MATRIX \u2014 unknown fields shown as \u2014");
    astra::setStyle(matrixCaption, 
        QString("QLabel { color: %1; font-size: 10px; font-weight: 500; "
                "letter-spacing: 0.08em; }").arg(kText2));
    matrixLay->addWidget(matrixCaption);

    mMatrix = new QTableWidget(matrixCard);
    mMatrix->setObjectName("timeframeMatrix");  // stable handle for tests
    mMatrix->setColumnCount(7);
    mMatrix->setHorizontalHeaderLabels(
        {"Timeframe", "Health", "Quality", "Sequence", "Freshness",
         "Last Closed Bar", "Signal/Setup"});
    mMatrix->setRowCount(9);
    const char* rowTfs[] = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"};
    for (int r = 0; r < 9; ++r) {
        QTableWidgetItem* tfItem = new QTableWidgetItem(QString::fromLatin1(rowTfs[r]));
        tfItem->setForeground(astra::uiColor(kText));
        QFont mono("JetBrains Mono", 12); mono.setWeight(QFont::DemiBold);
        tfItem->setFont(mono);
        mMatrix->setItem(r, 0, tfItem);
        // Every per-timeframe field is unavailable on the allowed endpoints
        // (/analysis/latest, /analysis/history, /context/latest, /health).
        for (int c = 1; c < 7; ++c) {
            QTableWidgetItem* item = new QTableWidgetItem(kEm);
            item->setForeground(astra::uiColor(kText3));
            QFont m2("JetBrains Mono", 12);
            item->setFont(m2);
            mMatrix->setItem(r, c, item);
        }
        mMatrix->setRowHeight(r, 24);
    }
    // Size the fixed columns to their widest header/cell (never clip a
    // header), and let the last column absorb the remainder so the table
    // never scrolls sideways.
    for (int c = 0; c < 6; ++c) {
        mMatrix->horizontalHeader()->setSectionResizeMode(c, QHeaderView::ResizeToContents);
    }
    mMatrix->horizontalHeader()->setStretchLastSection(true);
    mMatrix->verticalHeader()->setVisible(false);
    // QAbstractScrollArea::sizeHint() is small; ask for header + 9 rows so
    // the card grows instead of putting the matrix behind an inner scrollbar.
    mMatrix->setMinimumHeight(26 + 9 * 24 + 6);
    mMatrix->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    mMatrix->setSelectionMode(QAbstractItemView::NoSelection);
    mMatrix->setEditTriggers(QAbstractItemView::NoEditTriggers);
    mMatrix->setShowGrid(false);
    mMatrix->setFocusPolicy(Qt::NoFocus);
    astra::setStyle(mMatrix, 
        QString("QTableWidget { background: %1; border: 1px solid %2; "
                "border-radius: 8px; font-size: 12px; }"
                "QTableWidget::item { border-bottom: 1px solid %2; }"
                "QHeaderView::section { background: %2; color: %3; border: none; "
                "padding: 6px 4px; font-size: 9px; font-weight: 500; "
                "letter-spacing: 0.02em; }")
            .arg(kSurface, kSurface2, kText2));
    matrixLay->addWidget(mMatrix);

    // Deterministic column fit: size every column to its content, then give
    // the spare width to the last column (or scale all columns down if the
    // content is wider than the viewport). No sideways scrollbar, no clipped
    // headers. stretchLastSection keeps this correct on window resizes.
    QTimer::singleShot(250, mMatrix, [this]() {
        if (!mMatrix || !mMatrix->horizontalHeader()) return;
        QHeaderView* h = mMatrix->horizontalHeader();
        const int cols = mMatrix->columnCount();
        const int lastCol = cols - 1;

        // 1) Measure the content once...
        h->setStretchLastSection(false);
        mMatrix->resizeColumnsToContents();

        // ...2) freeze the modes so late ResizeToContents passes cannot
        //     re-measure and re-inflate the columns after we fit them...
        for (int c = 0; c < cols; ++c) {
            h->setSectionResizeMode(c, QHeaderView::Interactive);
        }

        // 3) fit: spare width goes to the last column, or scale everything
        //    down if the content is wider than the viewport.
        int total = 0;
        for (int c = 0; c < cols; ++c) total += h->sectionSize(c);
        const int vp = mMatrix->viewport()->width();
        if (vp > 0 && total < vp) {
            h->resizeSection(lastCol, h->sectionSize(lastCol) + (vp - total));
        } else if (vp > 0 && total > vp) {
            const double k = double(vp) / double(total);
            for (int c = 0; c < cols; ++c) {
                h->resizeSection(c, qMax(24, int(h->sectionSize(c) * k)));
            }
        }

        // 4) keep the last column filling remaining width on window resizes.
        h->setStretchLastSection(true);
    });
    row3->addWidget(matrixCard, 6);

    // Risk panel — placeholder
    QFrame* riskPanel = new QFrame(page);
    riskPanel->setProperty("astraCard", true);
    riskPanel->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* riskLay = new QVBoxLayout(riskPanel);
    riskLay->setContentsMargins(16, 14, 16, 16);
    riskLay->setSpacing(8);

    QLabel* riskCaption = new QLabel(riskPanel);
    riskCaption->setText("RISK PANEL");
    astra::setStyle(riskCaption, 
        QString("QLabel { color: %1; font-size: 10px; font-weight: 500; "
                "letter-spacing: 0.08em; }").arg(kText2));
    riskLay->addWidget(riskCaption);

    riskLay->addStretch();
    QLabel* riskBody = new QLabel(riskPanel);
    riskBody->setText("Enabled when the risk module ships.");
    riskBody->setAlignment(Qt::AlignCenter);
    astra::setStyle(riskBody, 
        QString("QLabel { color: %1; font-size: 13px; }").arg(kText3));
    riskLay->addWidget(riskBody);
    riskLay->addStretch();
    row3->addWidget(riskPanel, 4);

    mainLayout->addLayout(row3);

    // ─────────────── ROW 4 — 6 quick cards ───────────────
    QHBoxLayout* row4 = new QHBoxLayout();
    row4->setSpacing(12);
    const char* quickLabels[] = {"Research", "Knowledge", "Candidates",
                                 "Validation", "Approval Center", "Schedule"};
    for (int i = 0; i < 6; ++i) {
        QFrame* card = makeCard(page, QString::fromLatin1(quickLabels[i]).toUpper());
        QLabel* value = makeCardValue(card);
        astra::setStyle(value, 
            QString("QLabel { color: %1; font-size: 18px; font-weight: 600; "
                    "font-family: 'JetBrains Mono', 'Consolas', monospace; }").arg(kText3));
        card->layout()->addWidget(value);
        mQuickValues.append(value);
        row4->addWidget(card, 1);
    }
    mainLayout->addLayout(row4);

    // ─────────────── BOTTOM ACTION BAR ───────────────
    QHBoxLayout* actionBar = new QHBoxLayout();
    actionBar->setSpacing(8);
    actionBar->addStretch();

    auto makeAction = [actionBar, page](const QString& text, bool enabled) {
        QPushButton* b = new QPushButton(text, page);
        b->setFixedHeight(32);
        b->setEnabled(enabled);
        b->setAttribute(Qt::WA_AlwaysShowToolTips);
        if (!enabled) {
            b->setToolTip("Enabled when the backend supports this action.");
        }
        astra::setStyle(b, 
            QString("QPushButton { font-size: 12px; font-weight: 500; padding: 6px 14px; "
                    "border-radius: 8px; border: 1px solid %1; color: %2; "
                    "background: transparent; min-width: 0; }"
                    "QPushButton:hover:!disabled { background: %1; color: %3; }"
                    "QPushButton:disabled { color: %4; border-color: %1; }")
                .arg(kSurface2, kText2, kText, kText3));
        actionBar->addWidget(b);
        return b;
    };

    mRefreshBtn = makeAction("Refresh", true);
    mRefreshBtn->setObjectName("dashRefreshButton");
    makeAction("Checkpoint", false);
    makeAction("Pause", false);
    makeAction("Resume", false);
    makeAction("Stop", false);
    makeAction("Recovery", false);

    connect(mRefreshBtn, &QPushButton::clicked, this, [this]() {
        if (!mApiClient) return;
        mApiClient->fetchAnalysisLatest();
        mApiClient->fetchHealth();
        mApiClient->fetchAnalysisHistory(20);
    });

    mainLayout->addLayout(actionBar);
    mainLayout->addStretch();

    // Initial honest state — nothing claimed before the first response
    setCardValue(mHealthValue, kEm, kText3);
    mHealthSub->setText("waiting for /api/v1/health");
    setCardValue(mStreamsValue, kEm, kText3);
    mStreamsSub->setText("waiting for /api/v1/health");
    mSignalsStack->setCurrentIndex(0);  // signals empty state
}

// ── data updates ─────────────────────────────────────────────────────────────

void DashboardPage::setOnline(bool online) {
    mOnline = online;
    if (!online) {
        setCardValue(mHealthValue, "OFFLINE", kRed);
        mHealthSub->setText("backend unreachable");
        setCardValue(mStreamsValue, "OFFLINE", kRed);
        mStreamsSub->setText("bridge unreachable");
    } else if (mHasHealth) {
        updateFromHealth(mLastHealth);
    }
}

void DashboardPage::updateFromHealth(const HealthResponse& resp) {
    mLastHealth = resp;
    mHasHealth = true;
    mOnline = true;

    // SYSTEM HEALTH
    if (resp.data.status == "ok") {
        setCardValue(mHealthValue, "HEALTHY", kGreen);
        mHealthSub->setText("status ok");
    } else if (resp.data.status == "degraded") {
        setCardValue(mHealthValue, "DEGRADED", kAmber);
        mHealthSub->setText("status degraded");
    } else if (resp.data.status == "offline") {
        setCardValue(mHealthValue, "OFFLINE", kRed);
        mHealthSub->setText("status offline");
    } else {
        setCardValue(mHealthValue, kEm, kText3);
        mHealthSub->setText(kEm);
    }

    // DATA STREAMS — bridge status (no stream counts in the contract)
    if (resp.data.bridge == "ok") {
        setCardValue(mStreamsValue, "OK", kGreen);
        mStreamsSub->setText("bridge ok");
    } else if (resp.data.bridge == "stale") {
        setCardValue(mStreamsValue, "STALE", kAmber);
        mStreamsSub->setText("bridge stale");
    } else if (resp.data.bridge == "offline") {
        setCardValue(mStreamsValue, "OFFLINE", kRed);
        mStreamsSub->setText("bridge offline");
    } else {
        setCardValue(mStreamsValue, kEm, kText3);
        mStreamsSub->setText(kEm);
    }
}

void DashboardPage::updateFromAnalysis(const AnalysisResponse& resp) {
    // SIGNALS sub-caption: latest direction + raw score (never a probability)
    const Signal& sig = resp.data.signal;
    QString dir = sig.direction.isEmpty() ? QString(kEm) : sig.direction;
    mSignalsSub->setText(
        QString("latest %1 \u00B7 score %2").arg(dir).arg(sig.score, 0, 'f', 3));
    if (!mHasHistory) {
        setCardValue(mSignalsValue, kEm, kText3);
    }
}

void DashboardPage::updateFromHistory(const QVector<AnalysisData>& items) {
    mHistoryCount = items.size();
    mHasHistory = true;

    if (mHistoryCount == 0) {
        setCardValue(mSignalsValue, kEm, kText3);
        mSignalsStack->setCurrentIndex(0);  // empty state
        return;
    }

    setCardValue(mSignalsValue, QString::number(mHistoryCount), kAccent);
    if (mSignalsSub->text().isEmpty() || mSignalsSub->text() == kEm) {
        mSignalsSub->setText(QString("last %1").arg(mHistoryCount));
    }

    const int rows = qMin(20, mHistoryCount);
    mSignalsTable->setRowCount(rows);
    for (int i = 0; i < rows; ++i) {
        const AnalysisData& d = items[i];

        // Time — null timestamp renders as em dash
        QTableWidgetItem* timeItem = new QTableWidgetItem(
            d.timestamp.has_value() ? d.timestamp.value() : QString(kEm));
        timeItem->setForeground(astra::uiColor(kText2));
        mSignalsTable->setItem(i, 0, timeItem);

        // Direction
        QTableWidgetItem* dirItem;
        if (d.signal.direction == "UP") {
            dirItem = new QTableWidgetItem(QStringLiteral("\u25B2 UP"));
            dirItem->setForeground(astra::uiColor(kGreen));
        } else if (d.signal.direction == "DOWN") {
            dirItem = new QTableWidgetItem(QStringLiteral("\u25BC DOWN"));
            dirItem->setForeground(astra::uiColor(kRed));
        } else {
            dirItem = new QTableWidgetItem(kEm);
            dirItem->setForeground(astra::uiColor(kText3));
        }
        mSignalsTable->setItem(i, 1, dirItem);

        // Score — raw score, never labelled "probability"
        QTableWidgetItem* scoreItem = new QTableWidgetItem(
            QString::number(d.signal.score, 'f', 3));
        scoreItem->setForeground(astra::uiColor(kText));
        mSignalsTable->setItem(i, 2, scoreItem);

        // Coverage tier
        QTableWidgetItem* tierItem = new QTableWidgetItem(
            d.meta.coverageTier.isEmpty() ? QString(kEm) : d.meta.coverageTier);
        tierItem->setForeground(astra::uiColor(kText3));
        mSignalsTable->setItem(i, 3, tierItem);
    }
    mSignalsStack->setCurrentIndex(1);  // table
}

}  // namespace astra
