#include "TimeframeSwitcher.h"
#include "theme/Theme.h"

namespace astra {

TimeframeSwitcher::TimeframeSwitcher(QWidget* parent)
    : QWidget(parent)
    , mCurrentTf("M15")
{
    setFixedHeight(44);
    setupButtons();
    updateActiveButton();
}

void TimeframeSwitcher::setupButtons() {
    QHBoxLayout* layout = new QHBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(4);  // 4px gap between buttons

    for (int i = 0; i < 9; ++i) {
        QPushButton* btn = new QPushButton(this);
        btn->setText(TIMEFRAMES[i]);
        btn->setFixedSize(44, 32);
        astra::setStyle(btn, 
            "QPushButton { "
            "background: transparent; "
            "border: 1px solid #162A44; "
            "color: #8FA3BF; "
            "border-radius: 6px; "
            "font-size: 12px; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; "
            "font-weight: 500; "
            "}"
            "QPushButton:hover { "
            "background: #162A44; "
            "color: #E8EEF5; "
            "}"
            "QPushButton:pressed { "
            "background: #24384F; "
            "}"
        );
        layout->addWidget(btn);
        mButtons.append(btn);

        connect(btn, &QPushButton::clicked, this, [this, i]() {
            mCurrentTf = TIMEFRAMES[i];
            updateActiveButton();
            emit timeframeChanged(mCurrentTf);
        });
    }
}

void TimeframeSwitcher::setTimeframe(const QString& tf) {
    // Validate against known timeframes
    for (int i = 0; i < 9; ++i) {
        if (TIMEFRAMES[i] == tf) {
            mCurrentTf = tf;
            updateActiveButton();
            return;
        }
    }
}

void TimeframeSwitcher::resetToDefault() {
    setTimeframe("M15");
}

void TimeframeSwitcher::updateActiveButton() {
    for (int i = 0; i < mButtons.size(); ++i) {
        QPushButton* btn = mButtons[i];
        if (TIMEFRAMES[i] == mCurrentTf) {
            astra::setStyle(btn, 
                "QPushButton { "
                "background: #4A90D9; "
                "border: 1px solid #4A90D9; "
                "color: white; "
                "border-radius: 6px; "
                "font-size: 12px; "
                "font-family: 'JetBrains Mono', 'Consolas', monospace; "
                "font-weight: 500; "
                "}"
                "QPushButton:hover { "
                "background: #5AA0E9; "
                "}"
            );
        } else {
            astra::setStyle(btn, 
                "QPushButton { "
                "background: transparent; "
                "border: 1px solid #162A44; "
                "color: #8FA3BF; "
                "border-radius: 6px; "
                "font-size: 12px; "
                "font-family: 'JetBrains Mono', 'Consolas', monospace; "
                "font-weight: 500; "
                "}"
                "QPushButton:hover { "
                "background: #162A44; "
                "color: #E8EEF5; "
                "}"
            );
        }
    }
}

}  // namespace astra
