#include "RegimeChip.h"
#include "theme/Theme.h"

namespace astra {

RegimeChip::RegimeChip(QWidget* parent)
    : QWidget(parent)
{
    setFixedHeight(24);
    mLabel = new QLabel(this);
    astra::setStyle(mLabel, 
        "QLabel { "
        "color: #4A90D9; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "text-transform: uppercase; "
        "}"
    );
    mLabel->setAlignment(Qt::AlignCenter);

    QHBoxLayout* layout = new QHBoxLayout(this);
    layout->setContentsMargins(12, 4, 12, 4);
    layout->setSpacing(0);
    layout->addWidget(mLabel);

    // Default: RANGE style
    mRegime = "RANGE";
    updateStyle();
}

void RegimeChip::setRegime(const QString& regime) {
    mRegime = regime;
    // Normalize display name
    QString display = regime;
    if (display == "TREND_UP" || display == "TREND") display = "TREND";
    else if (display == "TREND_DOWN") display = "TREND";
    else if (display == "UNKNOWN") display = "UNKNOWN";
    else if (display == "NONE") display = "NONE";

    mLabel->setText(display);
    updateStyle();
}

void RegimeChip::updateStyle() {
    QString bgColor, textColor;
    QString regimeUpper = mRegime.toUpper();

    if (regimeUpper.contains("TREND") || regimeUpper == "UP" || regimeUpper == "BULLISH" || regimeUpper == "LONG") {
        bgColor = "rgba(76, 175, 122, 0.15)";
        textColor = "#4CAF7A";
    } else if (regimeUpper == "RANGE" || regimeUpper == "NEUTRAL" || regimeUpper == "NONE") {
        bgColor = "rgba(74, 144, 217, 0.15)";
        textColor = "#4A90D9";
    } else if (regimeUpper.contains("VOLATILE") || regimeUpper == "DOWN" || regimeUpper == "BEARISH" || regimeUpper == "SHORT") {
        bgColor = "rgba(217, 90, 90, 0.15)";
        textColor = "#D95A5A";
    } else if (regimeUpper == "QUIET") {
        bgColor = "rgba(184, 196, 212, 0.15)";
        textColor = "#B8C4D4";
    } else {
        // UNKNOWN or fallback
        bgColor = "rgba(90, 107, 128, 0.15)";
        textColor = "#5A6B80";
    }

    astra::setStyle(this, 
        QString("QWidget { "
                "background: %1; "
                "border-radius: 12px; "
                "}"
                "QLabel { "
                "color: %2; "
                "font-size: 12px; "
                "font-weight: 500; "
                "letter-spacing: 0.05em; "
                "text-transform: uppercase; "
                "}")
            .arg(bgColor)
            .arg(textColor)
    );
}

}  // namespace astra
