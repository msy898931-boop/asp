#include "SmcPage.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QJsonValue>
#include <QNetworkRequest>
#include <QUrl>
#include <QPalette>
#include <QTimer>

namespace astra {

namespace {

QString num(double v) {
    return QString::number(v, 'f', 2);
}

// The direction words the engine emits, mapped to a display label. Unknown
// directions fall through unchanged rather than being invented.
QString directionLabel(const QString& dir) {
    if (dir == "LONG") return "LONG";
    if (dir == "SHORT") return "SHORT";
    return "NEUTRAL";
}

}  // namespace

SmcPage::SmcPage(QWidget* parent)
    : QWidget(parent)
    , mEngineUrl("http://127.0.0.1:8795")
{
    setupLayout();
    restyle();
}

void SmcPage::changeEvent(QEvent* event) {
    if (event->type() == QEvent::PaletteChange) {
        restyle();
    }
    QWidget::changeEvent(event);
}

void SmcPage::setEngineUrl(const QString& url) {
    mEngineUrl = url.endsWith('/') ? url.left(url.size() - 1) : url;
}

void SmcPage::refresh() {
    fetch();
}

void SmcPage::setupLayout() {
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(24, 24, 24, 24);
    mainLayout->setSpacing(16);

    // ── Top row: title + bias chip + status + refresh ──
    QHBoxLayout* topRow = new QHBoxLayout();
    topRow->setSpacing(12);

    mTitle = new QLabel(this);
    mTitle->setText("SMC / ICT");
    topRow->addWidget(mTitle);

    mBiasChip = new QLabel(this);
    mBiasChip->setText("--");
    mBiasChip->setAlignment(Qt::AlignCenter);
    mBiasChip->setFixedHeight(26);
    topRow->addWidget(mBiasChip);

    mStatus = new QLabel(this);
    mStatus->setText("Not loaded");
    topRow->addWidget(mStatus);

    topRow->addStretch();

    mRefreshBtn = new QPushButton(this);
    mRefreshBtn->setText("Ask again");
    connect(mRefreshBtn, &QPushButton::clicked, this, &SmcPage::refresh);
    topRow->addWidget(mRefreshBtn);

    mainLayout->addLayout(topRow);

    // ── Market read card ──
    mReadCard = new QFrame(this);
    mReadCard->setProperty("astraCard", true);
    mReadCard->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* readLayout = new QVBoxLayout(mReadCard);
    readLayout->setContentsMargins(16, 16, 16, 16);
    readLayout->setSpacing(8);

    mReadTitle = new QLabel(mReadCard);
    mReadTitle->setText("MARKET READ");
    readLayout->addWidget(mReadTitle);

    mReadBody = new QLabel(mReadCard);
    mReadBody->setWordWrap(true);
    mReadBody->setText("Waiting for the isolated SMC engine.");
    readLayout->addWidget(mReadBody);

    mMeta = new QLabel(mReadCard);
    mMeta->setWordWrap(true);
    readLayout->addWidget(mMeta);

    mainLayout->addWidget(mReadCard);

    // ── Plans ──
    mPlansScroll = new QScrollArea(this);
    mPlansScroll->setWidgetResizable(true);
    mPlansScroll->setFrameShape(QFrame::NoFrame);
    mPlansScroll->setStyleSheet("QScrollArea { background: transparent; }");

    mPlansHost = new QWidget(mPlansScroll);
    mPlansLayout = new QVBoxLayout(mPlansHost);
    mPlansLayout->setContentsMargins(0, 0, 0, 0);
    mPlansLayout->setSpacing(12);
    mPlansLayout->addStretch();
    mPlansScroll->setWidget(mPlansHost);

    mainLayout->addWidget(mPlansScroll);
    mainLayout->setStretch(2, 1);
}

void SmcPage::fetch() {
    mStatus->setText("Reading...");
    QNetworkRequest request(QUrl(mEngineUrl + "/v1/smc?refresh=1"));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    QNetworkReply* reply = mNetwork.get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NetworkError::NoError) {
            SmcRead read;
            read.note = "SMC engine unreachable: " + reply->errorString()
                        + " (start it with ASTRA_ENABLE_SMC=1).";
            applyRead(read);
            return;
        }
        QJsonParseError err;
        const QJsonDocument doc = QJsonDocument::fromJson(reply->readAll(), &err);
        if (err.error != QJsonParseError::NoError) {
            SmcRead read;
            read.note = "SMC engine returned unreadable JSON: " + err.errorString();
            applyRead(read);
            return;
        }
        const QJsonObject smc = doc.object().value("smc").toObject();
        SmcRead read;
        read.configured = smc.value("configured").toBool(false);
        read.rateLimited = smc.value("rate_limited").toBool(false);
        read.authFailed = smc.value("auth_failed").toBool(false);
        read.model = smc.value("model").toString();
        read.note = smc.value("note").toString();

        const QJsonObject analysis = smc.value("analysis").toObject();
        read.hasAnalysis = !analysis.isEmpty();
        if (read.hasAnalysis) {
            read.bias = analysis.value("bias").toString("NEUTRAL").toUpper();
            read.marketRead = analysis.value("market_read").toString();
            read.newsCaution = analysis.value("news_caution").toBool(false);
            read.keyEvent = analysis.value("key_event").toString();

            const QJsonObject engine = analysis.value("engine").toObject();
            for (const QJsonValue& v : engine.value("timeframes_read").toArray()) {
                read.timeframesRead.append(v.toString());
            }
            for (const QJsonValue& v : analysis.value("rejected").toArray()) {
                read.rejectedReasons.append(v.toObject().value("reason").toString());
            }
            for (const QJsonValue& v : analysis.value("plans").toArray()) {
                const QJsonObject p = v.toObject();
                SmcPlan plan;
                plan.direction = p.value("direction").toString();
                plan.entry = p.value("entry").toDouble();
                plan.stop = p.value("stop").toDouble();
                for (const QJsonValue& t : p.value("targets").toArray()) {
                    plan.targets.append(t.toDouble());
                }
                plan.rewardRisk = p.value("reward_risk").toDouble();
                if (!p.value("confidence").isNull() &&
                    p.value("confidence").isUndefined() == false) {
                    plan.hasConfidence = true;
                    plan.confidence = p.value("confidence").toDouble();
                }
                plan.sweep = p.value("sweep").toString();
                plan.poi = p.value("poi").toString();
                plan.rationale = p.value("rationale").toString();
                read.plans.append(plan);
            }
        }
        applyRead(read);
    });
}

void SmcPage::applyRead(const SmcRead& read) {
    clearPlans();

    if (!read.configured) {
        mStatus->setText("Not configured");
        mBiasChip->setText("--");
        mReadBody->setText(read.note.isEmpty()
                               ? "SMC engine not configured: set the provider key "
                                 "(MISTRAL_API_KEY / GROQ_API_KEY / GEMINI_API_KEY)."
                               : read.note);
        mMeta->setText("");
        return;
    }
    if (read.rateLimited) {
        mStatus->setText("Paused (quota)");
        mBiasChip->setText("--");
        mReadBody->setText(read.note);
        mMeta->setText("");
        return;
    }
    if (read.authFailed) {
        // The key is set but the provider refused it. Distinct from "no reply"
        // and from "paused": the operator must fix the key, not wait.
        mStatus->setText("Key rejected");
        mBiasChip->setText("--");
        mReadBody->setText(read.note.isEmpty()
                               ? "SMC engine rejected by the provider: the key "
                                 "may be expired or revoked."
                               : read.note);
        mMeta->setText("");
        return;
    }
    if (!read.hasAnalysis) {
        mStatus->setText("Unavailable");
        mBiasChip->setText("--");
        mReadBody->setText(read.note.isEmpty()
                               ? "SMC engine unavailable: no usable reply."
                               : read.note);
        mMeta->setText("");
        return;
    }

    mStatus->setText("OK");
    mBiasChip->setText(directionLabel(read.bias));
    mReadBody->setText(read.marketRead);

    QStringList bits;
    if (!read.model.isEmpty()) bits << ("Model: " + read.model);
    if (!read.timeframesRead.isEmpty()) {
        bits << ("Frames read: " + read.timeframesRead.join(' '));
    }
    if (read.newsCaution) {
        bits << (read.keyEvent.isEmpty() ? "News caution"
                                         : "News caution: " + read.keyEvent);
    }
    if (!read.rejectedReasons.isEmpty()) {
        bits << QString("Rejected plans: %1").arg(read.rejectedReasons.size());
    }
    mMeta->setText(bits.join("  \u00B7  "));

    if (read.plans.isEmpty()) {
        QLabel* empty = new QLabel(mPlansHost);
        empty->setText("No coherent plan this read.");
        empty->setStyleSheet(QString("QLabel { color: %1; font-size: 14px; }")
                                 .arg(palette().color(QPalette::PlaceholderText).name()));
        mPlansLayout->insertWidget(mPlansLayout->count() - 1, empty);
    } else {
        int i = 1;
        for (const SmcPlan& plan : read.plans) {
            QFrame* card = addPlanCard(plan, i++);
            mPlansLayout->insertWidget(mPlansLayout->count() - 1, card);
        }
    }

    restyle();
}

void SmcPage::clearPlans() {
    // Everything except the trailing stretch.
    while (mPlansLayout->count() > 1) {
        QLayoutItem* item = mPlansLayout->takeAt(0);
        if (item->widget()) item->widget()->deleteLater();
        delete item;
    }
}

QFrame* SmcPage::addPlanCard(const SmcPlan& plan, int index) {
    const QPalette pal = palette();
    const QString textPrimary = pal.color(QPalette::Text).name();
    const QString textSecondary = pal.color(QPalette::WindowText).name();
    const QString surface = pal.color(QPalette::Base).name();
    const QString border = pal.color(QPalette::Mid).name();

    QFrame* card = new QFrame(mPlansHost);
    card->setProperty("astraCard", true);
    card->setFrameStyle(QFrame::NoFrame);
    QVBoxLayout* layout = new QVBoxLayout(card);
    layout->setContentsMargins(16, 16, 16, 16);
    layout->setSpacing(8);

    QLabel* head = new QLabel(card);
    head->setText(QString("%1. %2").arg(index).arg(directionLabel(plan.direction)));
    head->setStyleSheet(QString("QLabel { color: %1; font-size: 15px; font-weight: 600; }")
                            .arg(textPrimary));
    layout->addWidget(head);

    // Numbers grid: entry, stop, R:R, and the model's confidence (labelled an
    // estimate, because it is not a measured edge).
    QGridLayout* grid = new QGridLayout();
    grid->setHorizontalSpacing(24);
    grid->setVerticalSpacing(4);
    int row = 0;
    auto addRow = [&](const QString& caption, const QString& value) {
        QLabel* c = new QLabel(caption, card);
        c->setStyleSheet(QString("QLabel { color: %1; font-size: 12px; }")
                             .arg(textSecondary));
        QLabel* v = new QLabel(value, card);
        v->setStyleSheet(QString("QLabel { color: %1; font-size: 13px; "
                                 "font-family: 'JetBrains Mono', 'Consolas', monospace; }")
                             .arg(textPrimary));
        grid->addWidget(c, row, 0);
        grid->addWidget(v, row, 1);
        row++;
    };
    addRow("Entry", num(plan.entry));
    addRow("Stop", num(plan.stop));
    addRow("R:R", num(plan.rewardRisk));
    if (plan.hasConfidence) {
        addRow("Confidence (est.)",
               QString("%1%").arg(qRound(plan.confidence * 100.0)));
    }
    layout->addLayout(grid);

    if (!plan.sweep.isEmpty()) {
        QLabel* l = new QLabel(card);
        l->setWordWrap(true);
        l->setText("Sweep: " + plan.sweep);
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 13px; }").arg(textSecondary));
        layout->addWidget(l);
    }
    if (!plan.poi.isEmpty()) {
        QLabel* l = new QLabel(card);
        l->setWordWrap(true);
        l->setText("POI: " + plan.poi);
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 13px; }").arg(textSecondary));
        layout->addWidget(l);
    }

    if (!plan.targets.isEmpty()) {
        QStringList ladder;
        for (double t : plan.targets) ladder << num(t);
        QLabel* l = new QLabel(card);
        l->setWordWrap(true);
        l->setText(QString("Targets (%1): ").arg(plan.targets.size()) + ladder.join("  \u2192  "));
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 13px; "
                                 "font-family: 'JetBrains Mono', 'Consolas', monospace; }")
                             .arg(textPrimary));
        layout->addWidget(l);
    }

    if (!plan.rationale.isEmpty()) {
        QLabel* l = new QLabel(card);
        l->setWordWrap(true);
        l->setText(plan.rationale);
        l->setStyleSheet(QString("QLabel { color: %1; font-size: 13px; }").arg(textSecondary));
        layout->addWidget(l);
    }

    Q_UNUSED(surface);
    Q_UNUSED(border);
    return card;
}

void SmcPage::restyle() {
    const QPalette pal = palette();
    const QString textPrimary = pal.color(QPalette::Text).name();
    const QString textSecondary = pal.color(QPalette::WindowText).name();
    const QString textTertiary = pal.color(QPalette::PlaceholderText).name();
    const QString surfaceAlt = pal.color(QPalette::AlternateBase).name();
    const QString border = pal.color(QPalette::Mid).name();
    const QString accent = pal.color(QPalette::Highlight).name();

    if (mTitle) {
        mTitle->setStyleSheet(QString("QLabel { color: %1; font-size: 16px; "
                                      "font-weight: 600; }").arg(textPrimary));
    }
    if (mBiasChip) {
        mBiasChip->setStyleSheet(
            QString("QLabel { color: %1; font-size: 12px; font-weight: 600; "
                    "border: 1px solid %2; border-radius: 12px; padding: 2px 12px; "
                    "background: %3; }").arg(accent, border, surfaceAlt));
    }
    if (mStatus) {
        mStatus->setStyleSheet(QString("QLabel { color: %1; font-size: 13px; }")
                                   .arg(textSecondary));
    }
    if (mRefreshBtn) {
        mRefreshBtn->setStyleSheet(
            QString("QPushButton { background: transparent; border: 1px solid %1; "
                    "color: %2; border-radius: 8px; padding: 8px 16px; font-weight: 500; }"
                    "QPushButton:hover { background: %3; border-color: %4; color: %5; }")
                .arg(border, textSecondary, surfaceAlt, accent, textPrimary));
    }
    if (mReadTitle) {
        mReadTitle->setStyleSheet(QString("QLabel { color: %1; font-size: 12px; "
                                          "font-weight: 500; letter-spacing: 0.05em; }")
                                      .arg(textTertiary));
    }
    if (mReadBody) {
        mReadBody->setStyleSheet(QString("QLabel { color: %1; font-size: 14px; }")
                                     .arg(textPrimary));
    }
    if (mMeta) {
        mMeta->setStyleSheet(QString("QLabel { color: %1; font-size: 12px; }")
                                 .arg(textTertiary));
    }
}

}  // namespace astra
