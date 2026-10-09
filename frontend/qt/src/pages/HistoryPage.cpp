#include "HistoryPage.h"
#include "theme/Theme.h"
#include <QHeaderView>
#include <QDateTime>
#include <QFont>
#include <QMessageBox>

namespace astra {

HistoryPage::HistoryPage(QWidget* parent)
    : QWidget(parent)
{
    setupLayout();
}

void HistoryPage::setApiClient(ApiClient* client) {
    mApiClient = client;
}

void HistoryPage::setupLayout() {
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(24, 24, 24, 24);
    mainLayout->setSpacing(16);

    // Top row: title + filter + refresh
    QHBoxLayout* topRow = new QHBoxLayout();
    topRow->setSpacing(12);

    QLabel* titleLabel = new QLabel(this);
    titleLabel->setText("HISTORY");
    astra::setStyle(titleLabel, 
        "QLabel { "
        "color: #E8EEF5; "
        "font-size: 16px; "
        "font-weight: 600; "
        "}"
    );
    topRow->addWidget(titleLabel);

    // Filter: [All ▼] [Last 50 ▼]
    mFilterCombo = new QComboBox(this);
    mFilterCombo->addItem("All");
    mFilterCombo->addItem("Last 50");
    mFilterCombo->addItem("Last 20");
    mFilterCombo->addItem("Last 10");
    astra::setStyle(mFilterCombo, 
        "QComboBox { "
        "background: #0F1F35; "
        "border: 1px solid #162A44; "
        "color: #E8EEF5; "
        "border-radius: 8px; "
        "padding: 8px 12px; "
        "font-size: 14px; "
        "}"
        "QComboBox:hover { border-color: #4A90D9; }"
        "QComboBox:focus { border-color: #4A90D9; }"
    );
    topRow->addWidget(mFilterCombo);

    mRefreshBtn = new QPushButton(this);
    mRefreshBtn->setText("Refresh");
    astra::setStyle(mRefreshBtn, 
        "QPushButton { "
        "background: transparent; "
        "border: 1px solid #162A44; "
        "color: #8FA3BF; "
        "border-radius: 8px; "
        "padding: 8px 16px; "
        "font-weight: 500; "
        "}"
        "QPushButton:hover { "
        "background: #162A44; "
        "border-color: #4A90D9; "
        "color: #E8EEF5; "
        "}"
    );
    connect(mRefreshBtn, &QPushButton::clicked, this, &HistoryPage::refresh);
    topRow->addWidget(mRefreshBtn);

    topRow->addStretch();
    mainLayout->addLayout(topRow);

    // Table
    mTable = new QTableWidget(this);
    mTable->setColumnCount(5);
    mTable->setHorizontalHeaderLabels({"Time", "Dir", "Prob", "Tier", "Outcome"});
    mTable->setColumnWidth(0, 120);
    mTable->setColumnWidth(1, 80);
    mTable->setColumnWidth(2, 80);
    mTable->setColumnWidth(3, 100);
    mTable->setColumnWidth(4, 120);
    mTable->verticalHeader()->setDefaultSectionSize(36);
    mTable->setAlternatingRowColors(true);
    astra::setStyle(mTable->horizontalHeader(), 
        "QHeaderView::section { "
        "background: #162A44; "
        "color: #8FA3BF; "
        "padding: 10px 12px; "
        "border: none; "
        "border-bottom: 1px solid #24384F; "
        "font-size: 11px; "
        "font-weight: 500; "
        "text-transform: uppercase; "
        "letter-spacing: 0.05em; "
        "}"
    );
    astra::setStyle(mTable, 
        "QTableWidget { "
        "background: #0F1F35; "
        "border: 1px solid #162A44; "
        "border-radius: 12px; "
        "gridline-color: #162A44; "
        "}"
        "QTableWidget::item { "
        "padding: 8px 12px; "
        "border-bottom: 1px solid #162A44; "
        "font-size: 13px; "
        "font-family: 'JetBrains Mono', 'Consolas', monospace; "
        "}"
        "QTableWidget::item:alternate { "
        "background: #0D1930; "
        "}"
        "QTableWidget::item:selected { "
        "background: #162A44; "
        "color: #E8EEF5; "
        "}"
    );
    mTable->setSelectionMode(QAbstractItemView::NoSelection);
    mTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    mTable->verticalHeader()->setVisible(false);

    // Sorting by column click
    mTable->horizontalHeader()->setSectionsClickable(true);
    mainLayout->addWidget(mTable);

    // Summary cards
    QHBoxLayout* summaryRow = new QHBoxLayout();
    summaryRow->setSpacing(16);
    summaryRow->setStretch(0, 1);
    summaryRow->setStretch(1, 1);
    summaryRow->setStretch(2, 1);

    // Win Rate
    mSummaryCard1 = new QFrame(this);
    mSummaryCard1->setProperty("astraCard", true);
    mSummaryCard1->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* sr1 = new QVBoxLayout(mSummaryCard1);
    sr1->setContentsMargins(20, 16, 20, 16);
    QLabel* sr1Label = new QLabel(mSummaryCard1);
    sr1Label->setText("Win Rate");
    astra::setStyle(sr1Label, "QLabel { color: #8FA3BF; font-size: 12px; font-weight: 500; letter-spacing: 0.05em; }");
    sr1->addWidget(sr1Label);
    QLabel* sr1Value = new QLabel(mSummaryCard1);
    sr1Value->setText("—");
    astra::setStyle(sr1Value, "QLabel { color: #E8EEF5; font-size: 24px; font-weight: 600; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
    sr1->addWidget(sr1Value);
    summaryRow->addWidget(mSummaryCard1);

    // Total R
    mSummaryCard2 = new QFrame(this);
    mSummaryCard2->setProperty("astraCard", true);
    mSummaryCard2->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* sr2 = new QVBoxLayout(mSummaryCard2);
    sr2->setContentsMargins(20, 16, 20, 16);
    QLabel* sr2Label = new QLabel(mSummaryCard2);
    sr2Label->setText("Total R");
    astra::setStyle(sr2Label, "QLabel { color: #8FA3BF; font-size: 12px; font-weight: 500; letter-spacing: 0.05em; }");
    sr2->addWidget(sr2Label);
    QLabel* sr2Value = new QLabel(mSummaryCard2);
    sr2Value->setText("—");
    astra::setStyle(sr2Value, "QLabel { color: #E8EEF5; font-size: 24px; font-weight: 600; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
    sr2->addWidget(sr2Value);
    summaryRow->addWidget(mSummaryCard2);

    // PF
    mSummaryCard3 = new QFrame(this);
    mSummaryCard3->setProperty("astraCard", true);
    mSummaryCard3->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* sr3 = new QVBoxLayout(mSummaryCard3);
    sr3->setContentsMargins(20, 16, 20, 16);
    QLabel* sr3Label = new QLabel(mSummaryCard3);
    sr3Label->setText("PF");
    astra::setStyle(sr3Label, "QLabel { color: #8FA3BF; font-size: 12px; font-weight: 500; letter-spacing: 0.05em; }");
    sr3->addWidget(sr3Label);
    QLabel* sr3Value = new QLabel(mSummaryCard3);
    sr3Value->setText("—");
    astra::setStyle(sr3Value, "QLabel { color: #E8EEF5; font-size: 24px; font-weight: 600; font-family: 'JetBrains Mono', 'Consolas', monospace; }");
    sr3->addWidget(sr3Value);
    summaryRow->addWidget(mSummaryCard3);

    mainLayout->addLayout(summaryRow);

    // Initial empty state
    QVector<HistoryEntry> empty;
    populateTable(empty);
    updateSummary(empty);
}

void HistoryPage::updateFromHistory(const QVector<AnalysisData>& items) {
    // v1 SHADOW mode has no realised outcomes — nothing is fabricated here.
    QVector<HistoryEntry> entries;
    entries.reserve(items.size());
    for (const AnalysisData& d : items) {
        HistoryEntry e;
        e.timestamp = d.timestamp.has_value() ? d.timestamp.value() : QStringLiteral("\u2014");
        e.direction = d.signal.direction;
        e.score = d.signal.score;
        e.probabilityCalibrated = d.signal.probabilityCalibrated;
        e.coverageTier = d.meta.coverageTier;
        e.outcome = "pending";
        e.rMultiple = 0.0;
        entries.append(e);
    }
    populateTable(entries);
    updateSummary(entries);
}

void HistoryPage::refresh() {
    if (mApiClient) {
        mApiClient->fetchAnalysisHistory(50);
    }
}

void HistoryPage::populateTable(const QVector<HistoryEntry>& entries) {
    mTable->setRowCount(entries.size());
    for (int i = 0; i < entries.size(); ++i) {
        const auto& e = entries[i];

        // Time
        QTableWidgetItem* timeItem = new QTableWidgetItem(e.timestamp);
        timeItem->setForeground(astra::uiColor("#8FA3BF"));
        timeItem->setFont(QFont("JetBrains Mono", 13));
        mTable->setItem(i, 0, timeItem);

        // Dir — arrow + color
        QTableWidgetItem* dirItem = new QTableWidgetItem();
        QString arrow;
        QColor dirColor;
        if (e.direction == "UP" || e.direction == "LONG") {
            arrow = "\u25B2";
            dirColor = astra::uiColor("#4CAF7A");
        } else if (e.direction == "DOWN" || e.direction == "SHORT") {
            arrow = "\u25BC";
            dirColor = astra::uiColor("#D95A5A");
        } else {
            arrow = "\u2014";
            dirColor = astra::uiColor("#5A6B80");
        }
        dirItem->setText(arrow);
        dirItem->setForeground(dirColor);
        dirItem->setFont(QFont("JetBrains Mono", 13));
        mTable->setItem(i, 1, dirItem);

        // Prob — "63%" or "Score"
        QTableWidgetItem* probItem = new QTableWidgetItem();
        if (e.probabilityCalibrated) {
            probItem->setText(QString("%1%").arg(qRound(e.score * 100)));
        } else {
            probItem->setText("Score");
        }
        probItem->setForeground(astra::uiColor("#8FA3BF"));
        probItem->setFont(QFont("JetBrains Mono", 13));
        mTable->setItem(i, 2, probItem);

        // Tier
        QTableWidgetItem* tierItem = new QTableWidgetItem(e.coverageTier.toUpper());
        tierItem->setForeground(astra::uiColor("#5A6B80"));
        tierItem->setFont(QFont("JetBrains Mono", 13));
        mTable->setItem(i, 3, tierItem);

        // Outcome
        QTableWidgetItem* outcomeItem = new QTableWidgetItem();
        if (e.outcome == "win") {
            outcomeItem->setText("\u2713 +" + QString::number(e.rMultiple, 'f', 1) + "R");
            outcomeItem->setForeground(astra::uiColor("#4CAF7A"));
        } else if (e.outcome == "loss") {
            outcomeItem->setText("\u2717 " + QString::number(e.rMultiple, 'f', 1) + "R");
            outcomeItem->setForeground(astra::uiColor("#D95A5A"));
        } else {
            outcomeItem->setText("\u2014 pending");
            outcomeItem->setForeground(astra::uiColor("#5A6B80"));
        }
        outcomeItem->setFont(QFont("JetBrains Mono", 13));
        mTable->setItem(i, 4, outcomeItem);
    }
}

void HistoryPage::updateSummary(const QVector<HistoryEntry>& entries) {
    int wins = 0;
    int losses = 0;
    double totalR = 0;

    for (const auto& e : entries) {
        if (e.outcome == "win") {
            wins++;
            totalR += e.rMultiple;
        } else if (e.outcome == "loss") {
            losses++;
            totalR += e.rMultiple;
        }
    }

    int total = wins + losses;
    double winRate = total > 0 ? (double)wins / total * 100.0 : 0;

    // Update card values directly via saved pointers (set in constructor)
    // We need to find the value labels by position in the layout
    auto findValueLabel = [this](QFrame* card) -> QLabel* {
        QLayout* layout = card->layout();
        if (layout) {
            for (int i = 0; i < layout->count(); ++i) {
                QLayoutItem* item = layout->itemAt(i);
                if (item && item->widget()) {
                    QLabel* lbl = qobject_cast<QLabel*>(item->widget());
                    if (lbl && !lbl->text().isEmpty() && lbl->text() != "Win Rate" 
                        && lbl->text() != "Total R" && lbl->text() != "PF") {
                        return lbl;
                    }
                }
            }
        }
        return nullptr;
    };

    const QString em = QStringLiteral("\u2014");
    const QString mono =
        "font-size: 24px; font-weight: 600; font-family: 'JetBrains Mono', 'Consolas', monospace;";

    QLabel* winRateVal = findValueLabel(mSummaryCard1);
    QLabel* totalRVal = findValueLabel(mSummaryCard2);
    QLabel* pfVal = findValueLabel(mSummaryCard3);

    if (total == 0) {
        // No realised outcomes in SHADOW mode — every summary stays "—".
        if (winRateVal) {
            winRateVal->setText(em);
            astra::setStyle(winRateVal, QString("QLabel { color: #5A6B80; %1 }").arg(mono));
        }
        if (totalRVal) {
            totalRVal->setText(em);
            astra::setStyle(totalRVal, QString("QLabel { color: #5A6B80; %1 }").arg(mono));
        }
        if (pfVal) {
            pfVal->setText(em);
            astra::setStyle(pfVal, QString("QLabel { color: #5A6B80; %1 }").arg(mono));
        }
        return;
    }

    if (winRateVal) {
        winRateVal->setText(QString("%1%").arg(qRound(winRate)));
    }

    if (totalRVal) {
        totalRVal->setText(QString("%+1.%1R").arg(totalR, 0, 'f', 1));
        astra::setStyle(totalRVal, totalR < 0
            ? QString("QLabel { color: #D95A5A; %1 }").arg(mono)
            : QString("QLabel { color: #4CAF7A; %1 }").arg(mono));
    }

    if (pfVal) {
        double realizedPF = totalR / total;
        pfVal->setText(QString("1.%1").arg(qAbs(realizedPF), 0, 'f', 2));
    }
}

}  // namespace astra
