#include "SignalCard.h"
#include "theme/Theme.h"
#include <QTimer>
#include <QFont>

namespace astra {

SignalCard::SignalCard(QWidget* parent)
    : QWidget(parent)
{
    setupLayout();
}

void SignalCard::setupLayout() {
    QVBoxLayout* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->setSpacing(12);

    // Direction indicator — large ▲/▼/—
    mDirectionLabel = new QLabel(this);
    mDirectionLabel->setAlignment(Qt::AlignCenter);
    astra::setStyle(mDirectionLabel, "QLabel { font-size: 32px; font-weight: 600; }");
    layout->addWidget(mDirectionLabel);

    // Probability / Score
    QHBoxLayout* scoreRow = new QHBoxLayout();
    scoreRow->setSpacing(12);

    mLabelLabel = new QLabel(this);
    mLabelLabel->setText("SCORE");
    astra::setStyle(mLabelLabel, 
        "QLabel { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "}"
    );
    scoreRow->addWidget(mLabelLabel);

    mValueLabel = new QLabel(this);
    mValueLabel->setText("—");
    astra::setStyle(mValueLabel, 
        "QLabel { "
        "color: #E8EEF5; "
        "font-size: 32px; "
        "font-weight: 600; "
        "font-family: 'JetBrains Mono', 'Consolas', monospace; "
        "}"
    );
    scoreRow->addWidget(mValueLabel);

    layout->addLayout(scoreRow);

    // Bar
    mBar = new QProgressBar(this);
    mBar->setMaximum(100);
    mBar->setFixedHeight(6);
    astra::setStyle(mBar, 
        "QProgressBar { "
        "background: #162A44; "
        "border: none; "
        "border-radius: 3px; "
        "height: 6px; "
        "}"
        "QProgressBar::chunk { "
        "background: #5A6B80; "
        "border-radius: 3px; "
        "}"
    );
    layout->addWidget(mBar);

    // Confidence interval — hidden in uncalibrated mode
    mConfidenceLabel = new QLabel(this);
    mConfidenceLabel->setText("[\u2014 \u2014]");
    astra::setStyle(mConfidenceLabel, 
        "QLabel { "
        "color: #5A6B80; "
        "font-size: 14px; "
        "font-family: 'JetBrains Mono', 'Consolas', monospace; "
        "}"
    );
    mConfidenceLabel->setVisible(false);
    layout->addWidget(mConfidenceLabel);

    // Horizon — hidden in uncalibrated mode
    mHorizonLabel = new QLabel(this);
    mHorizonLabel->setText("Horizon: \u2014");
    astra::setStyle(mHorizonLabel, 
        "QLabel { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "}"
    );
    mHorizonLabel->setVisible(false);
    layout->addWidget(mHorizonLabel);

    // Model version — hidden in uncalibrated mode
    mModelLabel = new QLabel(this);
    mModelLabel->setText("Model: \u2014");
    astra::setStyle(mModelLabel, 
        "QLabel { "
        "color: #5A6B80; "
        "font-size: 12px; "
        "}"
    );
    mModelLabel->setVisible(false);
    layout->addWidget(mModelLabel);

    // Updated timestamp
    mUpdatedLabel = new QLabel(this);
    mUpdatedLabel->setText("Updated: just now");
    astra::setStyle(mUpdatedLabel, 
        "QLabel { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "}"
    );
    layout->addWidget(mUpdatedLabel);

    layout->addStretch();
}

void SignalCard::updateFromSignal(const Signal& signal, const Meta& meta) {
    updateDirection(signal.direction);
    updateScoreOrProbability(signal, meta);

    // Confidence, horizon, model version only shown when calibrated
    if (signal.probabilityCalibrated) {
        updateConfidence(signal);
        updateHorizon(signal);
        updateModelVersion(signal);
        mConfidenceLabel->setVisible(true);
        mHorizonLabel->setVisible(true);
        mModelLabel->setVisible(true);
    } else {
        mConfidenceLabel->setVisible(false);
        mHorizonLabel->setVisible(false);
        mModelLabel->setVisible(false);
    }

    // Updated time
    if (meta.dataFreshnessSec.has_value()) {
        mLastUpdateSec = meta.dataFreshnessSec.value();
        mUpdatedLabel->setText(QString("Updated: %1s ago").arg(mLastUpdateSec));
    } else {
        mUpdatedLabel->setText("Updated: \u2014");
    }
}

void SignalCard::updateDirection(const QString& direction) {
    if (direction == "UP" || direction == "LONG") {
        mDirectionLabel->setText("\u25B2 UP");
        astra::setStyle(mDirectionLabel, "QLabel { font-size: 32px; font-weight: 600; color: #4CAF7A; }");
    } else if (direction == "DOWN" || direction == "SHORT") {
        mDirectionLabel->setText("\u25BC DOWN");
        astra::setStyle(mDirectionLabel, "QLabel { font-size: 32px; font-weight: 600; color: #D95A5A; }");
    } else if (direction == "FLAT") {
        mDirectionLabel->setText("\u2014 FLAT");
        astra::setStyle(mDirectionLabel, "QLabel { font-size: 32px; font-weight: 600; color: #5A6B80; }");
    } else {
        mDirectionLabel->setText("\u2014 NONE");
        astra::setStyle(mDirectionLabel, "QLabel { font-size: 32px; font-weight: 600; color: #5A6B80; }");
    }
}

void SignalCard::updateScoreOrProbability(const Signal& signal, const Meta& meta) {
    if (signal.probabilityCalibrated && signal.probability.has_value()) {
        // Show probability
        double prob = signal.probability.value();
        int pct = qRound(qBound(0.0, prob, 1.0) * 100.0);
        mLabelLabel->setText("PROBABILITY");
        mValueLabel->setText(QString("%1%").arg(pct));

        // Bar color based on probability
        if (pct < 55) {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #5A6B80; border-radius: 3px; }");
        } else if (pct < 60) {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #D9A14A; border-radius: 3px; }");
        } else if (pct < 65) {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #4A90D9; border-radius: 3px; }");
        } else {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #4CAF7A; border-radius: 3px; }");
        }
        mBar->setValue(pct);
        mBar->setVisible(true);
    } else {
        // Show score (uncalibrated or missing probability)
        double score = signal.score;
        int pct = qRound(qBound(0.0, score, 1.0) * 100.0);
        mLabelLabel->setText("SCORE");
        mValueLabel->setText(QString("%1%").arg(pct));

        // Bar color based on score
        if (pct < 55) {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #5A6B80; border-radius: 3px; }");
        } else if (pct < 60) {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #D9A14A; border-radius: 3px; }");
        } else if (pct < 65) {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #4A90D9; border-radius: 3px; }");
        } else {
            astra::setStyle(mBar, "QProgressBar::chunk { background: #4CAF7A; border-radius: 3px; }");
        }
        mBar->setValue(pct);
        mBar->setVisible(true);
    }

    // Mute styling if degraded
    if (meta.degraded) {
        astra::setStyle(mValueLabel, 
            "QLabel { "
            "color: #D9A14A; "
            "font-size: 32px; "
            "font-weight: 600; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; "
            "}"
        );
    }
}

void SignalCard::updateConfidence(const Signal& signal) {
    if (signal.confidenceLo.has_value() && signal.confidenceHi.has_value()) {
        double lo = signal.confidenceLo.value();
        double hi = signal.confidenceHi.value();
        mConfidenceLabel->setText(
            QString("[%1 \u2014 %2]")
                .arg(QString::number(lo, 'f', 2))
                .arg(QString::number(hi, 'f', 2))
        );
        astra::setStyle(mConfidenceLabel, 
            "QLabel { "
            "color: #8FA3BF; "
            "font-size: 14px; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; "
            "}"
        );
    } else {
        mConfidenceLabel->setText("[\u2014 \u2014]");
    }
}

void SignalCard::updateHorizon(const Signal& signal) {
    if (signal.horizon.has_value()) {
        QString h = signal.horizon.value();
        mHorizonLabel->setText(QString("Horizon: %1").arg(h));
    } else {
        mHorizonLabel->setText("Horizon: \u2014");
    }
}

void SignalCard::updateModelVersion(const Signal& signal) {
    if (signal.modelVersion.has_value()) {
        mModelLabel->setText(QString("Model: %1").arg(signal.modelVersion.value()));
        astra::setStyle(mModelLabel, 
            "QLabel { "
            "color: #5A6B80; "
            "font-size: 12px; "
            "}"
        );
    } else {
        mModelLabel->setText("Model: \u2014");
    }
}

}  // namespace astra
