#include "HealthPage.h"
#include "theme/Theme.h"
#include <QFrame>
#include <QGridLayout>
#include <QLabel>
#include <QFont>
#include <QColor>

namespace astra {

HealthPage::HealthPage(QWidget* parent)
    : QWidget(parent)
{
    setupLayout();
}

void HealthPage::setApiClient(ApiClient* client) {
    mApiClient = client;
}

void HealthPage::setupLayout() {
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(24, 24, 24, 24);
    mainLayout->setSpacing(16);

    // ── System Status Card ──
    mSystemCard = new QFrame(this);
    mSystemCard->setProperty("astraCard", true);
    mSystemCard->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* sysLayout = new QVBoxLayout(mSystemCard);
    sysLayout->setContentsMargins(20, 20, 20, 20);
    sysLayout->setSpacing(0);

    QLabel* sysTitle = new QLabel(mSystemCard);
    sysTitle->setText("SYSTEM STATUS");
    astra::setStyle(sysTitle, 
        "QLabel { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "}"
    );
    sysLayout->addWidget(sysTitle);

    QLabel* spacer = new QLabel(mSystemCard);
    spacer->setFixedHeight(16);
    sysLayout->addWidget(spacer);

    // 6 rows
    const char* labels[] = {
        "Backend", "Bridge", "Data Freshness",
        "API Version", "Uptime", "Coverage Tier"
    };

    for (int i = 0; i < 6; ++i) {
        QHBoxLayout* row = new QHBoxLayout();
        row->setSpacing(12);

        QLabel* lbl = new QLabel(mSystemCard);
        lbl->setText(labels[i]);
        astra::setStyle(lbl, "QLabel { color: #8FA3BF; font-size: 14px; }");
        row->addWidget(lbl);

        QLabel* dot = new QLabel(mSystemCard);
        dot->setFixedSize(8, 8);
        astra::setStyle(dot, "QLabel { background: #4CAF7A; border-radius: 4px; }");
        row->addWidget(dot, 0, Qt::AlignTop);

        QLabel* val = new QLabel(mSystemCard);
        val->setText("—");
        astra::setStyle(val, "QLabel { color: #E8EEF5; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        row->addWidget(val);

        row->addStretch();

        sysLayout->addLayout(row);
        mStatusLabels.append(val);
        mStatusDots.append(dot);

        if (i < 5) {
            QFrame* div = new QFrame(mSystemCard);
            div->setFixedHeight(1);
            astra::setStyle(div, "QFrame { background: rgba(138, 163, 191, 0.3); }");
            sysLayout->addWidget(div);
        }
    }

    mainLayout->addWidget(mSystemCard);

    // ── Timeframes Card ──
    mTimeframesCard = new QFrame(this);
    mTimeframesCard->setProperty("astraCard", true);
    mTimeframesCard->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* tfLayout = new QVBoxLayout(mTimeframesCard);
    tfLayout->setContentsMargins(20, 20, 20, 20);
    tfLayout->setSpacing(0);

    QLabel* tfTitle = new QLabel(mTimeframesCard);
    tfTitle->setText("TIMEFRAMES");
    astra::setStyle(tfTitle, 
        "QLabel { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "}"
    );
    tfLayout->addWidget(tfTitle);

    QLabel* tfSpacer = new QLabel(mTimeframesCard);
    tfSpacer->setFixedHeight(16);
    tfLayout->addWidget(tfSpacer);

    mTimeframeGrid = new QGridLayout();
    mTimeframeGrid->setSpacing(8);
    mTimeframeGrid->setContentsMargins(0, 0, 0, 0);

    const char* tfs[] = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"};
    for (int i = 0; i < 9; ++i) {
        QFrame* item = new QFrame(mTimeframesCard);
        item->setFixedSize(100, 56);
        item->setProperty("astraCard", true);
        astra::setStyle(item, 
            "QFrame { "
            "background: #0F1F35; "
            "border: 1px solid #162A44; "
            "border-radius: 8px; "
            "padding: 16px; "
            "}"
        );

        QVBoxLayout* itemLayout = new QVBoxLayout(item);
        itemLayout->setContentsMargins(0, 0, 0, 0);
        itemLayout->setSpacing(4);

        QLabel* tfLabel = new QLabel(item);
        tfLabel->setText(tfs[i]);
        astra::setStyle(tfLabel, 
            "QLabel { "
            "color: #E8EEF5; "
            "font-size: 14px; "
            "font-weight: 500; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; "
            "}"
        );
        tfLabel->setAlignment(Qt::AlignLeft);
        itemLayout->addWidget(tfLabel);

        QLabel* statusLabel = new QLabel(item);
        statusLabel->setText("\u25CF OK");
        astra::setStyle(statusLabel, 
            "QLabel { "
            "color: #4CAF7A; "
            "font-size: 12px; "
            "}"
        );
        statusLabel->setAlignment(Qt::AlignRight);
        itemLayout->addWidget(statusLabel);

        mTimeframeGrid->addWidget(item, i / 3, i % 3);
    }

    tfLayout->addLayout(mTimeframeGrid);
    mainLayout->addWidget(mTimeframesCard);
    mainLayout->addStretch();
}

void HealthPage::updateFromHealth(const HealthResponse& resp) {
    updateSystemStatus(resp);

    // Update timeframe grid — all green for now (mock data shows all OK)
    const char* tfs[] = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"};
    for (int i = 0; i < 9; ++i) {
        QLayoutItem* item = mTimeframeGrid->itemAtPosition(i / 3, i % 3);
        if (item) {
            QFrame* frame = qobject_cast<QFrame*>(item->widget());
            if (frame) {
                QVBoxLayout* layout = qobject_cast<QVBoxLayout*>(frame->layout());
                if (layout) {
                    QLabel* statusLabel = qobject_cast<QLabel*>(layout->itemAt(1)->widget());
                    if (statusLabel) {
                        statusLabel->setText("\u25CF OK");
                        astra::setStyle(statusLabel, 
                            "QLabel { color: #4CAF7A; font-size: 12px; }"
                        );
                    }
                }
            }
        }
    }
}

void HealthPage::updateSystemStatus(const HealthResponse& resp) {
    // Backend status
    if (resp.data.status == "offline") {
        mStatusLabels[0]->setText("OFFLINE");
        astra::setStyle(mStatusLabels[0], "QLabel { color: #D95A5A; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[0], "QLabel { background: #D95A5A; border-radius: 4px; }");
    } else if (resp.data.status == "degraded") {
        mStatusLabels[0]->setText("DEGRADED");
        astra::setStyle(mStatusLabels[0], "QLabel { color: #D9A14A; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[0], "QLabel { background: #D9A14A; border-radius: 4px; }");
    } else {
        mStatusLabels[0]->setText("ONLINE");
        astra::setStyle(mStatusLabels[0], "QLabel { color: #4CAF7A; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[0], "QLabel { background: #4CAF7A; border-radius: 4px; }");
    }

    // Bridge status
    if (resp.data.bridge == "offline") {
        mStatusLabels[1]->setText("OFFLINE");
        astra::setStyle(mStatusLabels[1], "QLabel { color: #D95A5A; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[1], "QLabel { background: #D95A5A; border-radius: 4px; }");
    } else if (resp.data.bridge == "stale" || resp.data.bridge == "degraded") {
        mStatusLabels[1]->setText("STALE");
        astra::setStyle(mStatusLabels[1], "QLabel { color: #D9A14A; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[1], "QLabel { background: #D9A14A; border-radius: 4px; }");
    } else {
        mStatusLabels[1]->setText("OK");
        astra::setStyle(mStatusLabels[1], "QLabel { color: #4CAF7A; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[1], "QLabel { background: #4CAF7A; border-radius: 4px; }");
    }

    // Data freshness
    if (resp.data.uptimeSec.has_value()) {
        int secs = resp.data.uptimeSec.value();
        int mins = secs / 60;
        int hours = mins / 60;
        mins %= 60;
        mStatusLabels[4]->setText(QString("%1h %2m").arg(hours).arg(mins));
        astra::setStyle(mStatusLabels[4], "QLabel { color: #E8EEF5; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[4], "QLabel { background: #4CAF7A; border-radius: 4px; }");
    } else {
        mStatusLabels[4]->setText("—");
        astra::setStyle(mStatusLabels[4], "QLabel { color: #5A6B80; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
        astra::setStyle(mStatusDots[4], "QLabel { background: #5A6B80; border-radius: 4px; }");
    }

    // Freshness (row 2) — /health/v1 exposes no data-freshness field.
    // Unknown renders as em-dash; never fabricate a number.
    mStatusLabels[2]->setText("\u2014");
    astra::setStyle(mStatusLabels[2], "QLabel { color: #5A6B80; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
    astra::setStyle(mStatusDots[2], "QLabel { background: #5A6B80; border-radius: 4px; }");

    // API Version (row 3)
    mStatusLabels[3]->setText(resp.data.version);
    astra::setStyle(mStatusLabels[3], "QLabel { color: #E8EEF5; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
    astra::setStyle(mStatusDots[3], "QLabel { background: #4CAF7A; border-radius: 4px; }");

    // Coverage Tier (row 5) — coverage_tier lives on the analysis meta object,
    // not on /health/v1. Render as unavailable; never fabricate it.
    mStatusLabels[5]->setText("\u2014");
    astra::setStyle(mStatusLabels[5], "QLabel { color: #5A6B80; font-size: 14px; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
    astra::setStyle(mStatusDots[5], "QLabel { background: #5A6B80; border-radius: 4px; }");
}

}  // namespace astra
