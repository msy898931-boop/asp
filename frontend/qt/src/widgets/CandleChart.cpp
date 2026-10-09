#include "CandleChart.h"
#include "theme/Theme.h"
#include <QPainter>
#include <QPen>
#include <QBrush>
#include <QFont>
#include <QWheelEvent>
#include <QMouseEvent>
#include <QTimer>
#include <cmath>
#include <algorithm>

namespace astra {

// Color constants (from ASTRA visual identity)
static QColor COLORS_BULL() { return astra::uiColor("#4CAF7A"); }
static QColor COLORS_BEAR() { return astra::uiColor("#D95A5A"); }
static QColor COLORS_GRID() { return astra::uiColor("#162A44"); }
static QColor COLORS_ACCENT() { return astra::uiColor("#4A90D9"); }
static QColor COLORS_TEXT_SECONDARY() { return astra::uiColor("#8FA3BF"); }
static QColor COLORS_TEXT_PRIMARY() { return astra::uiColor("#E8EEF5"); }
static QColor COLORS_SURFACE() { return astra::uiColor("#0F1F35"); }
static QColor COLORS_SURFACE_2() { return astra::uiColor("#162A44"); }

CandleChart::CandleChart(QWidget* parent)
    : QWidget(parent)
    , mVisibleCount(100)
{
    setMouseTracking(true);
    setFocusPolicy(Qt::StrongFocus);

    // Initial price range (will be recalculated when candles arrive)
    mPriceMin = 2640.0;
    mPriceMax = 2660.0;
    mPriceRange = mPriceMax - mPriceMin;
}

void CandleChart::setCandles(const QVector<CandleData>& candles) {
    mCandles = candles;
    if (!mCandles.isEmpty()) {
        // Calculate price range from data
        double minP = mCandles[0].low;
        double maxP = mCandles[0].high;
        for (const auto& c : mCandles) {
            minP = std::min(minP, c.low);
            maxP = std::max(maxP, c.high);
        }
        // Add 2% padding
        double padding = (maxP - minP) * 0.02;
        mPriceMin = minP - padding;
        mPriceMax = maxP + padding;
        mPriceRange = mPriceMax - mPriceMin;
        mLastClose = mCandles.last().close;
    }
    mVisibleStart = qMax(0, mCandles.size() - mVisibleCount);
    updateGeometry();
    update();
}

void CandleChart::setAnalysisLevels(double sl, double tp) {
    mSL = sl;
    mTP = tp;
    mShowSLTP = (sl > 0 && tp > 0);
    update();
}

void CandleChart::zoomIn() {
    int newCount = qMax(mMinVisible, mVisibleCount - 20);
    if (newCount != mVisibleCount) {
        mVisibleCount = newCount;
        mVisibleStart = qMax(0, mCandles.size() - mVisibleCount);
        updateGeometry();
        update();
    }
}

void CandleChart::zoomOut() {
    int newCount = qMin(mMaxVisible, mVisibleCount + 20);
    if (newCount != mVisibleCount) {
        mVisibleCount = newCount;
        mVisibleStart = qMax(0, mCandles.size() - mVisibleCount);
        updateGeometry();
        update();
    }
}

void CandleChart::resetZoom() {
    mVisibleCount = 100;
    mVisibleStart = qMax(0, mCandles.size() - mVisibleCount);
    mIsPanned = false;
    updateGeometry();
    update();
}

void CandleChart::goToLive() {
    resetZoom();
}

void CandleChart::updateGeometry() {
    if (mCandles.isEmpty()) return;

    mChartLeft = 0;
    mChartTop = 0;
    mChartWidth = width() - mPriceAxisWidth - mGap;
    mChartHeight = height() - mTimeAxisHeight - mGap;

    // Candle width: available width / visible count, minus gap
    double availableWidth = mChartWidth - 2 * mGap;  // padding
    mCandleWidth = qMax(1.0, (availableWidth / mVisibleCount) * 0.8);  // 80% body, 20% gap
    mCandleGap = mCandleWidth * 0.25;  // 25% of width as gap
}

void CandleChart::paintEvent(QPaintEvent* /*event*/) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, false);  // HD 3000: no antialiasing

    // Background
    p.fillRect(rect(), astra::uiColor("#0A1628"));

    if (mCandles.isEmpty()) {
        p.setPen(QPen(COLORS_TEXT_SECONDARY(), 1));
        p.setFont(QFont("Inter", 14));
        p.drawText(rect().adjusted(20, 0, -20, 0),
                   Qt::AlignCenter,
                   "No candle data available\nConnect to backend to see chart");
        return;
    }

    drawChart(p);
}

void CandleChart::drawChart(QPainter& p) {
    // Clip to chart area
    QRectF clipRect(mChartLeft + mGap, mChartTop + mGap,
                    mChartWidth - 2 * mGap, mChartHeight - 2 * mGap);
    p.save();
    p.setClipRect(clipRect);

    drawGrid(p);
    drawCandles(p);
    drawLastPriceLine(p);
    drawSLTPLines(p);
    drawCrosshair(p);

    p.restore();

    // Price axis
    drawPriceAxis(p);
    // Time axis
    drawTimeAxis(p);
}

void CandleChart::drawGrid(QPainter& p) {
    p.setPen(QPen(COLORS_GRID(), 1, Qt::DotLine));
    p.setOpacity(0.3);

    // Horizontal grid lines — every ~5% of price range
    double range = mPriceMax - mPriceMin;
    int numLines = qMax(4, qRound(range / (range * 0.05)));
    for (int i = 0; i <= numLines; ++i) {
        double price = mPriceMin + (range * i / numLines);
        double y = mChartTop + mGap + (1.0 - (price - mPriceMin) / range) * (mChartHeight - 2 * mGap);
        p.drawLine(mChartLeft + mGap, y, mChartLeft + mChartWidth - mGap, y);
    }

    // Vertical grid lines — every ~10 candles
    int visibleCandles = qMin(mVisibleCount, mCandles.size());
    int numVLines = qMax(4, visibleCandles / 10);
    for (int i = 0; i <= numVLines; ++i) {
        double x = mChartLeft + mGap + (mChartWidth - 2 * mGap) * i / numVLines;
        p.drawLine(x, mChartTop + mGap, x, mChartTop + mChartHeight - mGap);
    }

    p.setOpacity(1.0);
}

void CandleChart::drawCandles(QPainter& p) {
    int visibleCandles = qMin(mVisibleCount, mCandles.size());
    int startIdx = mVisibleStart;
    int endIdx = qMin(startIdx + visibleCandles, mCandles.size());

    double x = mChartLeft + mGap;

    for (int i = startIdx; i < endIdx; ++i) {
        const CandleData& c = mCandles[i];

        double bodyTop = mChartTop + mGap +
            (1.0 - (c.close - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);
        double bodyBottom = mChartTop + mGap +
            (1.0 - (c.open - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);

        double highY = mChartTop + mGap +
            (1.0 - (c.high - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);
        double lowY = mChartTop + mGap +
            (1.0 - (c.low - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);

        bool isBull = c.close >= c.open;
        QColor candleColor = isBull ? COLORS_BULL() : COLORS_BEAR();

        // Wick
        p.setPen(QPen(candleColor, 1));
        p.drawLine(QPointF(x + mCandleWidth / 2, highY),
                   QPointF(x + mCandleWidth / 2, lowY));

        // Body
        double bodyHeight = qAbs(bodyTop - bodyBottom);
        p.setBrush(isBull ? Qt::NoBrush : QBrush(candleColor));
        p.setPen(Qt::NoPen);

        QRectF bodyRect(x, qMin(bodyTop, bodyBottom),
                        mCandleWidth, qMax(1.0, bodyHeight));
        p.drawRect(bodyRect);

        // Bull body is hollow (no fill), bear body is filled
        // (Already handled by brush above)

        // Gap between candles
        x += mCandleWidth + mCandleGap;
    }
}

void CandleChart::drawPriceAxis(QPainter& p) {
    // Background
    p.fillRect(mChartLeft + mChartWidth + mGap / 2, mChartTop,
               mPriceAxisWidth, mChartHeight,
               COLORS_SURFACE());

    // Divider line
    p.setPen(QPen(COLORS_GRID(), 1));
    p.drawLine(mChartLeft + mChartWidth + mGap / 2, mChartTop,
               mChartLeft + mChartWidth + mGap / 2, mChartTop + mChartHeight);

    // Labels — right-aligned
    p.setPen(QPen(COLORS_TEXT_SECONDARY(), 1));
    p.setFont(QFont("JetBrains Mono", 11));

    double range = mPriceMax - mPriceMin;
    double step = range / 5.0;
    int precision = (mPriceMax >= 1000) ? 2 : 4;

    for (int i = 0; i <= 5; ++i) {
        double price = mPriceMin + step * i;
        double y = mChartTop + mGap + (1.0 - (price - mPriceMin) / range) * (mChartHeight - 2 * mGap);

        QString label = QString::number(price, 'f', precision);
        p.drawText(QRectF(mChartLeft + mChartWidth + mGap / 2,
                          y - 8,
                          mPriceAxisWidth,
                          16),
                   Qt::AlignRight | Qt::AlignVCenter, label);
    }
}

void CandleChart::drawTimeAxis(QPainter& p) {
    // Background
    p.fillRect(mChartLeft, mChartTop + mChartHeight + mGap / 2,
               mChartWidth, mTimeAxisHeight,
               COLORS_SURFACE());

    // Divider line
    p.setPen(QPen(COLORS_GRID(), 1));
    p.drawLine(mChartLeft, mChartTop + mChartHeight + mGap / 2,
               mChartLeft + mChartWidth, mChartTop + mChartHeight + mGap / 2);

    // Labels — centered under candles
    p.setPen(QPen(COLORS_TEXT_SECONDARY(), 1));
    p.setFont(QFont("JetBrains Mono", 11));

    int visibleCandles = qMin(mVisibleCount, mCandles.size());
    int startIdx = mVisibleStart;
    int endIdx = qMin(startIdx + visibleCandles, mCandles.size());
    int numLabels = qMax(1, qMin(visibleCandles / 10, 6));

    double x = mChartLeft + mGap;
    double stepX = (mChartWidth - 2 * mGap) / qMax(1, numLabels - 1);

    for (int i = 0; i < numLabels; ++i) {
        int candleIdx = startIdx + (visibleCandles * i / numLabels);
        if (candleIdx >= endIdx) break;

        double px = mChartLeft + mGap + (mChartWidth - 2 * mGap) * i / (numLabels - 1);
        QString timeLabel = mCandles[candleIdx].timeLabel;
        p.drawText(QRectF(px - 30, mChartTop + mChartHeight + mGap / 2,
                          60, mTimeAxisHeight),
                   Qt::AlignCenter, timeLabel);
    }
}

void CandleChart::drawLastPriceLine(QPainter& p) {
    if (mCandles.isEmpty()) return;

    double lastPrice = mCandles.last().close;
    double y = mChartTop + mGap +
        (1.0 - (lastPrice - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);

    // Dashed line
    QPen pen(COLORS_ACCENT(), 1, Qt::DashLine);
    p.setPen(pen);
    p.drawLine(mChartLeft + mGap, y, mChartLeft + mChartWidth - mGap, y);

    // Label on price axis
    p.fillRect(mChartLeft + mChartWidth + mGap / 2 + 2, y - 8,
               mPriceAxisWidth - 4, 16,
               COLORS_ACCENT());
    p.setPen(QPen(Qt::white, 1));
    p.setFont(QFont("JetBrains Mono", 11));
    QString label = QString::number(lastPrice, 'f', 2);
    p.drawText(QRectF(mChartLeft + mChartWidth + mGap / 2 + 4, y - 8,
                      mPriceAxisWidth - 8, 16),
               Qt::AlignRight | Qt::AlignVCenter, label);
}

void CandleChart::drawSLTPLines(QPainter& p) {
    if (!mShowSLTP) return;

    // SL line (bear color, dashed)
    if (mSL > 0 && mSL >= mPriceMin && mSL <= mPriceMax) {
        double y = mChartTop + mGap +
            (1.0 - (mSL - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);
        QPen pen(COLORS_BEAR(), 1, Qt::DashLine);
        p.setPen(pen);
        p.drawLine(mChartLeft + mGap, y, mChartLeft + mChartWidth - mGap, y);
        // Label
        p.fillRect(mChartLeft + mChartWidth + mGap / 2 + 2, y - 8,
                   mPriceAxisWidth - 4, 16,
                   COLORS_BEAR());
        p.setPen(QPen(Qt::white, 1));
        p.drawText(QRectF(mChartLeft + mChartWidth + mGap / 2 + 4, y - 8,
                          mPriceAxisWidth - 8, 16),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString::number(mSL, 'f', 2));
    }

    // TP line (bull color, dashed)
    if (mTP > 0 && mTP >= mPriceMin && mTP <= mPriceMax) {
        double y = mChartTop + mGap +
            (1.0 - (mTP - mPriceMin) / mPriceRange) * (mChartHeight - 2 * mGap);
        QPen pen(COLORS_BULL(), 1, Qt::DashLine);
        p.setPen(pen);
        p.drawLine(mChartLeft + mGap, y, mChartLeft + mChartWidth - mGap, y);
        // Label
        p.fillRect(mChartLeft + mChartWidth + mGap / 2 + 2, y - 8,
                   mPriceAxisWidth - 4, 16,
                   COLORS_BULL());
        p.setPen(QPen(Qt::white, 1));
        p.drawText(QRectF(mChartLeft + mChartWidth + mGap / 2 + 4, y - 8,
                          mPriceAxisWidth - 8, 16),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString::number(mTP, 'f', 2));
    }
}

void CandleChart::drawCrosshair(QPainter& p) {
    if (!mShowCrosshair) return;

    // Vertical line
    p.setPen(QPen(COLORS_TEXT_SECONDARY(), 1, Qt::DashLine));
    p.setOpacity(0.5);
    p.drawLine(mCrosshairX, mChartTop + mGap,
               mCrosshairX, mChartTop + mChartHeight - mGap);

    // Horizontal line
    p.drawLine(mChartLeft + mGap, mCrosshairY,
               mChartLeft + mChartWidth - mGap, mCrosshairY);
    p.setOpacity(1.0);

    // OHLC box
    if (mHasHoverData) {
        drawOHCLBox(p, QPointF(mCrosshairX, mCrosshairY), mHoveredCandle);
    }
}

void CandleChart::drawOHCLBox(QPainter& p, const QPointF& pos, const CandleData& candle) {
    QRectF box(pos.x() + 10, pos.y() - 80, 150, 72);
    box.adjust(0, 0, 0, 0);

    // Clamp to chart area
    if (box.right() > mChartLeft + mChartWidth - mGap) {
        box.setLeft(pos.x() - 160);
    }

    // Background
    p.fillRect(box, COLORS_SURFACE_2());
    p.setPen(QPen(COLORS_GRID(), 1));
    p.drawRect(box);

    p.setPen(QPen(COLORS_TEXT_PRIMARY(), 1));
    p.setFont(QFont("JetBrains Mono", 12));

    int y = box.top() + 12;
    p.drawText(box.left() + 8, y, QString("Time: %1").arg(candle.timeLabel));
    y += 16;
    p.drawText(box.left() + 8, y, QString("O: %1").arg(QString::number(candle.open, 'f', 2)));
    y += 16;
    p.drawText(box.left() + 8, y, QString("H: %1").arg(QString::number(candle.high, 'f', 2)));
    y += 16;
    p.drawText(box.left() + 8, y, QString("L: %1").arg(QString::number(candle.low, 'f', 2)));
    y += 16;
    p.drawText(box.left() + 8, y, QString("C: %1").arg(QString::number(candle.close, 'f', 2)));
    y += 16;
    p.drawText(box.left() + 8, y, QString("V: %1").arg(QString::number(candle.volume, 'f', 0)));
}

void CandleChart::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        // Check if inside chart area
        if (event->x() >= mChartLeft + mGap &&
            event->x() <= mChartLeft + mChartWidth - mGap &&
            event->y() >= mChartTop + mGap &&
            event->y() <= mChartTop + mChartHeight - mGap) {
            mIsPanning = true;
            mPanStartX = event->x();
            mPanStartIndex = mVisibleStart;
            setCursor(Qt::SizeHorCursor);
        }
    }
    QWidget::mousePressEvent(event);
}

void CandleChart::mouseMoveEvent(QMouseEvent* event) {
    // Update crosshair
    if (event->x() >= mChartLeft + mGap &&
        event->x() <= mChartLeft + mChartWidth - mGap &&
        event->y() >= mChartTop + mGap &&
        event->y() <= mChartTop + mChartHeight - mGap) {
        mShowCrosshair = true;
        mCrosshairX = event->x();
        mCrosshairY = event->y();

        // Find which candle this x corresponds to
        if (!mCandles.isEmpty()) {
            int visibleCandles = qMin(mVisibleCount, mCandles.size());
            double relX = (event->x() - mChartLeft - mGap) / (mChartWidth - 2 * mGap);
            int candleIdx = mVisibleStart + qRound(relX * visibleCandles);
            candleIdx = qBound(mVisibleStart, candleIdx, mCandles.size() - 1);
            mHoveredCandle = mCandles[candleIdx];
            mHasHoverData = true;
            setCursor(Qt::CrossCursor);
        }
        update();
    } else {
        mShowCrosshair = false;
        setCursor(Qt::ArrowCursor);
        update();
    }

    // Pan
    if (mIsPanning) {
        int delta = event->x() - mPanStartX;
        double candleWidth = (mChartWidth - 2 * mGap) / mVisibleCount;
        int indexDelta = qRound(delta / candleWidth);
        mVisibleStart = qBound(0, mPanStartIndex - indexDelta, qMax(0, mCandles.size() - mVisibleCount));
        mIsPanned = (mVisibleStart > 0);
        update();
    }
}

void CandleChart::mouseReleaseEvent(QMouseEvent* /*event*/) {
    mIsPanning = false;
    setCursor(Qt::ArrowCursor);
}

void CandleChart::wheelEvent(QWheelEvent* event) {
    // Zoom
    if (event->angleDelta().y() > 0) {
        zoomIn();
    } else {
        zoomOut();
    }
    event->accept();
}

void CandleChart::resizeEvent(QResizeEvent* /*event*/) {
    updateGeometry();
    update();
}

}  // namespace astra
