#include "LevelsCard.h"
#include "theme/Theme.h"
#include <QFrame>
#include <QHBoxLayout>

namespace astra {

LevelsCard::LevelsCard(QWidget* parent)
    : QWidget(parent)
{
    setupLayout();
}

void LevelsCard::setupLayout() {
    QVBoxLayout* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(0);

    mBodyLayout = new QVBoxLayout();
    mBodyLayout->setSpacing(0);
    layout->addLayout(mBodyLayout);
}

void LevelsCard::updateFromLevels(const Levels& levels) {
    // Check if ALL are null
    bool allNull = !levels.entry.has_value()
        && !levels.stopLoss.has_value()
        && !levels.takeProfit.has_value()
        && !levels.rewardRisk.has_value()
        && !levels.suggestedRiskPct.has_value();

    if (allNull) {
        showAllUnavailable();
    } else {
        showLevels(levels);
    }
}

void LevelsCard::showAllUnavailable() {
    // Clear existing
    QLayoutItem* item;
    while ((item = mBodyLayout->takeAt(0)) != nullptr) {
        if (item->widget()) item->widget()->deleteLater();
        delete item;
    }

    QLabel* msg = new QLabel(this);
    msg->setText("Levels unavailable \u2014 calibration pending");
    astra::setStyle(msg, 
        "QLabel { "
        "color: #5A6B80; "
        "font-size: 14px; "
        "}"
    );
    msg->setAlignment(Qt::AlignCenter);
    msg->setWordWrap(true);
    mBodyLayout->addWidget(msg);
}

void LevelsCard::showLevels(const Levels& levels) {
    // Clear existing
    QLayoutItem* item;
    while ((item = mBodyLayout->takeAt(0)) != nullptr) {
        if (item->widget()) item->widget()->deleteLater();
        delete item;
    }

    auto addRow = [&](const QString& label, const QString& value,
                       const QString& color = "#E8EEF5", bool isDivider = false) {
        if (isDivider) {
            QFrame* div = new QFrame(this);
            div->setFixedHeight(1);
            astra::setStyle(div, "QFrame { background: rgba(138, 163, 191, 0.3); }");
            mBodyLayout->addWidget(div);
            return;
        }

        QHBoxLayout* row = new QHBoxLayout();
        row->setSpacing(12);

        QLabel* lbl = new QLabel(this);
        lbl->setText(label);
        astra::setStyle(lbl, "QLabel { color: #8FA3BF; font-size: 14px; }");

        QLabel* val = new QLabel(this);
        val->setText(value);
        astra::setStyle(val, 
            QString("QLabel { color: %1; font-size: 14px; "
                    "font-family: 'JetBrains Mono', 'Consolas', monospace; }")
                .arg(color));
        val->setAlignment(Qt::AlignRight | Qt::AlignVCenter);

        row->addWidget(lbl);
        row->addWidget(val, 0, Qt::AlignRight);
        mBodyLayout->addLayout(row);
    };

    // Entry
    if (levels.entry.has_value()) {
        addRow("Entry", QString::number(levels.entry.value(), 'f', 2));
    }

    // Stop Loss with distance
    if (levels.entry.has_value() && levels.stopLoss.has_value()) {
        double entry = levels.entry.value();
        double sl = levels.stopLoss.value();
        double dist = qAbs(entry - sl);
        QString distStr = QString("%1 (%2)")
                               .arg(QString::number(dist, 'f', 2))
                               .arg(dist < 0 ? "\u25BC" : "\u25BC");  // always red for SL
        addRow("Stop Loss", QString::number(sl, 'f', 2) + "  \u2014  " + distStr,
               "#D95A5A");
    } else if (levels.stopLoss.has_value()) {
        addRow("Stop Loss", QString::number(levels.stopLoss.value(), 'f', 2), "#D95A5A");
    }

    // Take Profit with distance
    if (levels.entry.has_value() && levels.takeProfit.has_value()) {
        double entry = levels.entry.value();
        double tp = levels.takeProfit.value();
        double dist = qAbs(tp - entry);
        addRow("Take Profit", QString::number(tp, 'f', 2) + "  \u2014  " +
               QString("%1 (%2)").arg(QString::number(dist, 'f', 2)).arg("\u25B2"),
               "#4CAF7A");
    } else if (levels.takeProfit.has_value()) {
        addRow("Take Profit", QString::number(levels.takeProfit.value(), 'f', 2), "#4CAF7A");
    }

    // Divider before RR
    addRow("", "", "#E8EEF5", true);

    // Reward/Risk
    if (levels.rewardRisk.has_value()) {
        addRow("Reward/Risk", QString("%1 : 1").arg(
                   QString::number(levels.rewardRisk.value(), 'f', 2)));
    }

    // Suggested Risk
    if (levels.suggestedRiskPct.has_value()) {
        addRow("Suggested Risk", QString("%1%").arg(
                   QString::number(levels.suggestedRiskPct.value() * 100.0, 'f', 1)));
    }

    // Methods (if available)
    if (levels.slMethod.has_value() || levels.tpMethod.has_value()) {
        addRow("", "", "#E8EEF5", true);
        if (levels.slMethod.has_value()) {
            addRow("SL Method", levels.slMethod.value(), "#8FA3BF");
        }
        if (levels.tpMethod.has_value()) {
            addRow("TP Method", levels.tpMethod.value(), "#8FA3BF");
        }
    }
}

}  // namespace astra
