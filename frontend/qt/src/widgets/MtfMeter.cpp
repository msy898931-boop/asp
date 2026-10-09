#include "MtfMeter.h"
#include "theme/Theme.h"

namespace astra {

MtfMeter::MtfMeter(QWidget* parent)
    : QWidget(parent)
{
    setFixedHeight(24);

    mBar = new QLabel(this);
    mBar->setFixedHeight(6);
    astra::setStyle(mBar, "QLabel { background: #162A44; border-radius: 3px; }");
    mBar->setFixedWidth(0);

    mValueLabel = new QLabel(this);
    mValueLabel->setText("N/A");
    astra::setStyle(mValueLabel, 
        "QLabel { "
        "color: #5A6B80; "
        "font-size: 14px; "
        "font-family: 'JetBrains Mono', 'Consolas', monospace; "
        "}"
    );

    QVBoxLayout* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(4);
    layout->addWidget(mBar);
    layout->addWidget(mValueLabel);

    setVisible(false);
}

void MtfMeter::setAgreement(double value) {
    int pct = qRound(qBound(0.0, value, 1.0) * 100.0);
    mValueLabel->setText(QString("%1%").arg(pct));

    // Bar width proportional (max ~120px)
    int barWidth = qRound(pct * 1.2);
    mBar->setFixedWidth(qMax(4, barWidth));

    // Color
    QString color;
    if (pct < 50) {
        color = "#5A6B80";  // text-tertiary
    } else if (pct < 70) {
        color = "#D9A14A";  // warning
    } else if (pct < 85) {
        color = "#4A90D9";  // accent-blue
    } else {
        color = "#4CAF7A";  // bull
    }

    astra::setStyle(mBar, QString("QLabel { background: %1; border-radius: 3px; }").arg(color));
    astra::setStyle(mValueLabel, 
        QString("QLabel { "
                "color: %1; "
                "font-size: 14px; "
                "font-family: 'JetBrains Mono', 'Consolas', monospace; "
                "}")
            .arg(color)
    );
}

}  // namespace astra
