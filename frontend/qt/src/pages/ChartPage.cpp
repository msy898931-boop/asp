#include "ChartPage.h"
#include "theme/Theme.h"
#include <QComboBox>
#include <QPushButton>
#include <QTimer>
#include <QApplication>

namespace astra {

ChartPage::ChartPage(QWidget* parent)
    : QWidget(parent)
    , mCurrentTf("M15")
{
    setupLayout();
}

void ChartPage::setApiClient(ApiClient* client) {
    mApiClient = client;
    // Note: /api/v1/candles endpoint does NOT exist yet.
    // When it's added, connect to fetch candles on tf change.
}

void ChartPage::setupLayout() {
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(24, 24, 24, 24);
    mainLayout->setSpacing(0);

    // Top row (60px)
    mTopRow = new QFrame(this);
    mTopRow->setFixedHeight(60);
    QHBoxLayout* topLayout = new QHBoxLayout(mTopRow);
    topLayout->setContentsMargins(0, 0, 0, 0);
    topLayout->setSpacing(12);

    // Title
    mChartTitle = new QLabel(mTopRow);
    mChartTitle->setText("CHART");
    astra::setStyle(mChartTitle, 
        "QLabel { "
        "color: #E8EEF5; "
        "font-size: 16px; "
        "font-weight: 600; "
        "}"
    );
    topLayout->addWidget(mChartTitle);

    // Spacer
    QSpacerItem* spacer = new QSpacerItem(20, 20, QSizePolicy::Expanding, QSizePolicy::Minimum);
    topLayout->addSpacerItem(spacer);

    // Timeframe switcher
    mTfSwitcher = new TimeframeSwitcher(mTopRow);
    connect(mTfSwitcher, &TimeframeSwitcher::timeframeChanged,
            this, &ChartPage::updateChartForTimeframe);
    topLayout->addWidget(mTfSwitcher);

    mainLayout->addWidget(mTopRow);

    // Chart area — fills remaining space
    mChart = new CandleChart(this);
    mChart->setFocusPolicy(Qt::StrongFocus);
    mainLayout->addWidget(mChart);
    mainLayout->setStretch(1, 1);

    // Show stub message since /candles endpoint is missing
    mChart->update();
}

void ChartPage::updateChartForTimeframe(const QString& tf) {
    mCurrentTf = tf;
    // /api/v1/candles endpoint does NOT exist.
    // When it's added, call:
    //   mApiClient->fetchCandles(tf, 500);
    //
    // For now, show stub data for visual testing.
    // Use last closed bar from /timeframes/{tf}/snapshot as a single candle
    // to show the chart widget is functional.
    if (mApiClient) {
        // We could poll /timeframes/{tf}/snapshot to get the latest bar
        // but for now, just show an empty chart with the stub message
    }
}

void ChartPage::onTimeframeKey(int index) {
    if (index >= 0 && index <= 8) {
        const char* tfs[] = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"};
        mTfSwitcher->setTimeframe(tfs[index]);
    }
}

void ChartPage::zoomIn() {
    mChart->zoomIn();
}

void ChartPage::zoomOut() {
    mChart->zoomOut();
}

void ChartPage::resetZoom() {
    mChart->resetZoom();
}

}  // namespace astra
