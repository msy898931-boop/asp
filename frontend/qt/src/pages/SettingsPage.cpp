#include "SettingsPage.h"
#include "theme/Theme.h"
#include <QGroupBox>
#include <QFormLayout>
#include <QTimer>
#include <QFont>

namespace astra {

SettingsPage::SettingsPage(QWidget* parent)
    : QWidget(parent)
    , mSettings("ASTRA", "Desktop")
{
    setupLayout();
    loadSettings();
}

void SettingsPage::setApiClient(ApiClient* client) {
    mApiClient = client;
}

void SettingsPage::setupLayout() {
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    mainLayout->setContentsMargins(24, 24, 24, 24);
    mainLayout->setSpacing(16);

    // ── GENERAL ──
    QGroupBox* generalGroup = new QGroupBox(this);
    generalGroup->setTitle("GENERAL");
    astra::setStyle(generalGroup, 
        "QGroupBox { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "padding-top: 20px; "
        "margin-top: 0; "
        "}"
        "QGroupBox:::title { "
        "subcontrol-origin: margin; "
        "left: 20px; "
        "}"
    );
    QFormLayout* generalLayout = new QFormLayout(generalGroup);
    generalLayout->setSpacing(12);
    generalLayout->setLabelAlignment(Qt::AlignLeft);

    mApiUrlEdit = new QLineEdit(generalGroup);
    mApiUrlEdit->setText("http://127.0.0.1:8790/api/v1/");
    astra::setStyle(mApiUrlEdit, 
        "QLineEdit { "
        "background: #0F1F35; "
        "border: 1px solid #162A44; "
        "color: #E8EEF5; "
        "border-radius: 8px; "
        "padding: 8px 12px; "
        "font-size: 14px; "
        "font-family: 'JetBrains Mono', 'Consolas', monospace; "
        "}"
        "QLineEdit:hover { border-color: #24384F; }"
        "QLineEdit:focus { border-color: #4A90D9; }"
    );
    generalLayout->addRow("API Base URL", mApiUrlEdit);

    mRefreshCombo = new QComboBox(generalGroup);
    mRefreshCombo->addItem("5s");
    mRefreshCombo->addItem("10s");
    mRefreshCombo->addItem("15s");
    mRefreshCombo->addItem("30s");
    astra::setStyle(mRefreshCombo, 
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
    generalLayout->addRow("Refresh Interval", mRefreshCombo);

    mFullscreenCheck = new QCheckBox(generalGroup);
    mFullscreenCheck->setText("Start in Fullscreen");
    astra::setStyle(mFullscreenCheck, 
        "QCheckBox { color: #E8EEF5; spacing: 8px; }"
        "QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #162A44; border-radius: 3px; background: #0F1F35; }"
        "QCheckBox::indicator:checked { background: #4A90D9; border-color: #4A90D9; }"
    );
    generalLayout->addRow(mFullscreenCheck);

    mCrosshairCheck = new QCheckBox(generalGroup);
    mCrosshairCheck->setText("Show Crosshair");
    mCrosshairCheck->setChecked(true);
    astra::setStyle(mCrosshairCheck, 
        "QCheckBox { color: #E8EEF5; spacing: 8px; }"
        "QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #162A44; border-radius: 3px; background: #0F1F35; }"
        "QCheckBox::indicator:checked { background: #4A90D9; border-color: #4A90D9; }"
    );
    generalLayout->addRow(mCrosshairCheck);

    mSlTpCheck = new QCheckBox(generalGroup);
    mSlTpCheck->setText("Show SL/TP Overlay");
    mSlTpCheck->setChecked(true);
    astra::setStyle(mSlTpCheck, 
        "QCheckBox { color: #E8EEF5; spacing: 8px; }"
        "QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #162A44; border-radius: 3px; background: #0F1F35; }"
        "QCheckBox::indicator:checked { background: #4A90D9; border-color: #4A90D9; }"
    );
    generalLayout->addRow(mSlTpCheck);

    mainLayout->addWidget(generalGroup);

    // ── APPEARANCE ──
    QGroupBox* appearanceGroup = new QGroupBox(this);
    appearanceGroup->setTitle("APPEARANCE");
    astra::setStyle(appearanceGroup, 
        "QGroupBox { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "padding-top: 20px; "
        "margin-top: 0; "
        "}"
        "QGroupBox:::title { "
        "subcontrol-origin: margin; "
        "left: 20px; "
        "}"
    );
    QFormLayout* appLayout = new QFormLayout(appearanceGroup);
    appLayout->setSpacing(12);

    // Theme: Dark / Light radio buttons
    QHBoxLayout* themeRow = new QHBoxLayout();
    themeRow->setSpacing(24);

    mThemeDarkRadio = new QLabel(appearanceGroup);
    mThemeDarkRadio->setText("\u25A1 Dark");
    astra::setStyle(mThemeDarkRadio, 
        "QLabel { color: #E8EEF5; font-size: 14px; }"
        "QLabel:hover { color: #4A90D9; }"
    );
    themeRow->addWidget(mThemeDarkRadio);

    mThemeLightRadio = new QLabel(appearanceGroup);
    mThemeLightRadio = new QLabel(appearanceGroup);
    mThemeLightRadio->setText("\u25A1 Light");
    astra::setStyle(mThemeLightRadio, 
        "QLabel { color: #8FA3BF; font-size: 14px; }"
    );
    themeRow->addWidget(mThemeLightRadio);

    appLayout->addRow("Theme", themeRow);

    // Font size
    mFontSizeCombo = new QComboBox(appearanceGroup);
    mFontSizeCombo->addItem("Small");
    mFontSizeCombo->addItem("Normal");
    mFontSizeCombo->addItem("Large");
    astra::setStyle(mFontSizeCombo, 
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
    appLayout->addRow("Font Size", mFontSizeCombo);

    mainLayout->addWidget(appearanceGroup);

    // ── KEYBOARD SHORTCUTS (read-only) ──
    QGroupBox* shortcutsGroup = new QGroupBox(this);
    shortcutsGroup->setTitle("KEYBOARD SHORTCUTS");
    astra::setStyle(shortcutsGroup, 
        "QGroupBox { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "padding-top: 20px; "
        "margin-top: 0; "
        "}"
        "QGroupBox:::title { "
        "subcontrol-origin: margin; "
        "left: 20px; "
        "}"
    );
    QGridLayout* shortcutLayout = new QGridLayout(shortcutsGroup);
    shortcutLayout->setSpacing(4);
    shortcutLayout->setContentsMargins(20, 16, 20, 16);

    struct Shortcut { const char* key; const char* desc; };
    Shortcut shortcuts[] = {
        {"F11", "Toggle fullscreen"},
        {"ESC", "Exit fullscreen / cancel"},
        {"Ctrl+Q", "Exit"},
        {"Ctrl+R", "Refresh"},
        {"Ctrl+D", "Dashboard"},
        {"Ctrl+H", "Chart"},
        {"Ctrl+L", "History"},
        {"Ctrl+K", "Health"},
        {"Ctrl+,", "Settings"},
        {"1..9", "Timeframe"},
        {"Ctrl+T", "Toggle theme"},
    };

    for (int i = 0; i < 11; ++i) {
        QLabel* keyLbl = new QLabel(shortcutsGroup);
        keyLbl->setText(shortcuts[i].key);
        astra::setStyle(keyLbl, 
            "QLabel { "
            "color: #4A90D9; "
            "font-size: 13px; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; "
            "font-weight: 500; "
            "}"
        );

        QLabel* descLbl = new QLabel(shortcutsGroup);
        descLbl->setText(shortcuts[i].desc);
        astra::setStyle(descLbl, 
            "QLabel { color: #8FA3BF; font-size: 13px; }"
        );

        shortcutLayout->addWidget(keyLbl, i, 0);
        shortcutLayout->addWidget(descLbl, i, 1);
        shortcutLayout->setColumnMinimumWidth(0, 80);
    }

    mainLayout->addWidget(shortcutsGroup);

    // ── ABOUT ──
    QGroupBox* aboutGroup = new QGroupBox(this);
    aboutGroup->setTitle("ABOUT");
    astra::setStyle(aboutGroup, 
        "QGroupBox { "
        "color: #8FA3BF; "
        "font-size: 12px; "
        "font-weight: 500; "
        "letter-spacing: 0.05em; "
        "padding-top: 20px; "
        "margin-top: 0; "
        "}"
        "QGroupBox:::title { "
        "subcontrol-origin: margin; "
        "left: 20px; "
        "}"
    );
    QVBoxLayout* aboutLayout = new QVBoxLayout(aboutGroup);
    aboutLayout->setContentsMargins(20, 16, 20, 16);
    aboutLayout->setSpacing(4);

    QLabel* about1 = new QLabel(aboutGroup);
    about1->setText("ASTRA Desktop v1.0");
    astra::setStyle(about1, "QLabel { color: #E8EEF5; font-size: 14px; font-weight: 500; }");
    aboutLayout->addWidget(about1);

    QLabel* about2 = new QLabel(aboutGroup);
    about2->setText("Backend API v1");
    astra::setStyle(about2, "QLabel { color: #8FA3BF; font-size: 14px; }");
    aboutLayout->addWidget(about2);

    QLabel* about3 = new QLabel(aboutGroup);
    about3->setText("Decision support only. Not financial advice.");
    astra::setStyle(about3, "QLabel { color: #5A6B80; font-size: 12px; }");
    aboutLayout->addWidget(about3);

    mainLayout->addWidget(aboutGroup);

    // ── Save button ──
    mSaveBtn = new QPushButton(this);
    mSaveBtn->setText("Save");
    mSaveBtn->setProperty("primary", true);
    astra::setStyle(mSaveBtn, 
        "QPushButton { "
        "background: #4A90D9; "
        "color: white; "
        "border: 1px solid #4A90D9; "
        "border-radius: 8px; "
        "padding: 12px 24px; "
        "font-size: 14px; "
        "font-weight: 500; "
        "}"
        "QPushButton:hover { background: #5AA0E9; }"
        "QPushButton:pressed { background: #3A80C9; }"
    );
    connect(mSaveBtn, &QPushButton::clicked, this, &SettingsPage::saveSettings);
    mainLayout->addWidget(mSaveBtn);
    mainLayout->addStretch();

    // Toast (hidden by default)
    mToastFrame = new QFrame(this);
    mToastFrame->setProperty("astraCard", true);
    mToastFrame->setFrameStyle(QFrame::NoFrame);
    mToastFrame->setFixedHeight(40);
    astra::setStyle(mToastFrame, 
        "QFrame { "
        "background: #0F1F35; "
        "border: 1px solid #4CAF7A; "
        "border-radius: 8px; "
        "}"
    );
    mToastLabel = new QLabel(mToastFrame);
    mToastLabel->setText("");
    astra::setStyle(mToastLabel, 
        "QLabel { color: #4CAF7A; font-size: 14px; }"
    );
    mToastLabel->setAlignment(Qt::AlignCenter);
    QHBoxLayout* toastLayout = new QHBoxLayout(mToastFrame);
    toastLayout->setContentsMargins(16, 0, 16, 0);
    toastLayout->addWidget(mToastLabel);
    mToastFrame->setVisible(false);
    mainLayout->addWidget(mToastFrame);
}

void SettingsPage::loadSettings() {
    mApiUrlEdit->setText(mSettings.value("apiUrl", "http://127.0.0.1:8790/api/v1/").toString());
    int refreshIdx = mSettings.value("refreshInterval", 0).toInt();
    mRefreshCombo->setCurrentIndex(qBound(0, refreshIdx, mRefreshCombo->count() - 1));
    mFullscreenCheck->setChecked(mSettings.value("startFullscreen", false).toBool());
    mCrosshairCheck->setChecked(mSettings.value("showCrosshair", true).toBool());
    mSlTpCheck->setChecked(mSettings.value("showSlTp", true).toBool());
    int themeIdx = mSettings.value("theme", 0).toInt();
    // 0 = dark, 1 = light
    astra::setStyle(mThemeDarkRadio, themeIdx == 0
        ? "QLabel { color: #4A90D9; font-size: 14px; }"
        : "QLabel { color: #8FA3BF; font-size: 14px; }");
    astra::setStyle(mThemeLightRadio, themeIdx == 1
        ? "QLabel { color: #4A90D9; font-size: 14px; }"
        : "QLabel { color: #8FA3BF; font-size: 14px; }");
    int fontIdx = mSettings.value("fontSize", 1).toInt();
    mFontSizeCombo->setCurrentIndex(qBound(0, fontIdx, mFontSizeCombo->count() - 1));
}

void SettingsPage::saveSettings() {
    mSettings.setValue("apiUrl", mApiUrlEdit->text());
    mSettings.setValue("refreshInterval", mRefreshCombo->currentIndex());
    mSettings.setValue("startFullscreen", mFullscreenCheck->isChecked());
    mSettings.setValue("showCrosshair", mCrosshairCheck->isChecked());
    mSettings.setValue("showSlTp", mSlTpCheck->isChecked());
    mSettings.setValue("fontSize", mFontSizeCombo->currentIndex());

    // Theme (0=dark, 1=light)
    int themeIdx = (mThemeDarkRadio->styleSheet().contains("color: #4A90D9"))
        ? 0 : 1;
    mSettings.setValue("theme", themeIdx);

    showToast("Settings saved");
}

void SettingsPage::showToast(const QString& message) {
    mToastLabel->setText(message);
    mToastFrame->setVisible(true);
    QTimer::singleShot(2000, this, [this]() {
        mToastFrame->setVisible(false);
    });
}

}  // namespace astra
