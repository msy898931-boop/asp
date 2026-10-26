#pragma once
#include <QWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QLabel>
#include <QFrame>
#include <QPushButton>
#include <QScrollArea>
#include <QVector>
#include <QEvent>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QSettings>

namespace astra {

// ──────────────────────────────────────────────────────────────────────────────
// SmcPage — the isolated SMC/ICT analysis panel.
//
// The SMC engine is its own process on its own port (8795) and answers
// {"smc": {...}} on /v1/smc. It is deliberately NOT part of the frozen v1
// contract, so this page talks to it directly instead of through the backend
// host: nothing about the validated pipeline is touched. If the engine is off
// or unreachable the page says so and shows nothing else — every number it does
// show is the model's own claim, labelled an estimate, never a measured edge.
//
// Fields mirror advisor/smc_engine.py: bias, market_read, plans (entry, stop,
// targets, reward_risk, confidence), sweep, poi, rationale, and the engine
// block (timeframes read, min R:R, rejected plans).
// ──────────────────────────────────────────────────────────────────────────────

struct SmcPlan {
    QString direction;            // "LONG" | "SHORT"
    double entry = 0.0;
    double stop = 0.0;
    QVector<double> targets;
    double rewardRisk = 0.0;
    bool hasConfidence = false;
    double confidence = 0.0;      // model's estimate, not measured
    QString sweep;
    QString poi;
    QString rationale;
};

struct SmcRead {
    bool configured = false;
    bool rateLimited = false;
    bool authFailed = false;
    bool hasAnalysis = false;
    QString bias;                 // "LONG" | "SHORT" | "NEUTRAL"
    QString marketRead;
    QString model;
    QString note;
    bool newsCaution = false;
    QString keyEvent;
    QVector<QString> timeframesRead;
    QVector<QString> rejectedReasons;
    QVector<SmcPlan> plans;
};

class SmcPage : public QWidget {
    Q_OBJECT

public:
    explicit SmcPage(QWidget* parent = nullptr);

    // Points the page at the isolated engine (default http://127.0.0.1:8795).
    void setEngineUrl(const QString& url);
    void refresh();

protected:
    void changeEvent(QEvent* event) override;

private:
    void setupLayout();
    void restyle();
    void fetch();
    void applyRead(const SmcRead& read);
    void clearPlans();
    QFrame* addPlanCard(const SmcPlan& plan, int index);

    QLabel* mTitle = nullptr;
    QLabel* mBiasChip = nullptr;
    QLabel* mStatus = nullptr;
    QPushButton* mRefreshBtn = nullptr;
    QFrame* mReadCard = nullptr;
    QLabel* mReadTitle = nullptr;
    QLabel* mReadBody = nullptr;
    QLabel* mMeta = nullptr;
    QScrollArea* mPlansScroll = nullptr;
    QWidget* mPlansHost = nullptr;
    QVBoxLayout* mPlansLayout = nullptr;

    QVector<QFrame*> mDividers;

    QNetworkAccessManager mNetwork;
    QString mEngineUrl;
    QSettings mSettings;
};

}  // namespace astra
