#include "ComingSoonPage.h"
#include "theme/Theme.h"
#include <QVBoxLayout>
#include <QHBoxLayout>

namespace astra {

ComingSoonPage::ComingSoonPage(QWidget* parent)
    : QWidget(parent)
{
    setObjectName("comingSoonPage");  // stable handle for tests
    QVBoxLayout* layout = new QVBoxLayout(this);
    layout->setContentsMargins(24, 24, 24, 24);
    layout->setSpacing(8);
    layout->addStretch();

    QHBoxLayout* row = new QHBoxLayout();
    row->addStretch();

    QVBoxLayout* center = new QVBoxLayout();
    center->setSpacing(10);

    mTitle = new QLabel(this);
    mTitle->setObjectName("comingSoonTitle");
    mTitle->setAlignment(Qt::AlignHCenter);
    astra::setStyle(mTitle, 
        "QLabel { "
        "color: #E8EEF5; "
        "font-size: 22px; "
        "font-weight: 600; "
        "letter-spacing: 1px; "
        "}"
    );
    center->addWidget(mTitle);

    QLabel* badge = new QLabel(this);
    badge->setText("COMING SOON");
    badge->setAlignment(Qt::AlignHCenter);
    astra::setStyle(badge, 
        "QLabel { "
        "color: #4A90D9; "
        "font-size: 11px; "
        "font-weight: 500; "
        "letter-spacing: 0.08em; "
        "border: 1px solid #162A44; "
        "border-radius: 8px; "
        "padding: 4px 12px; "
        "background: #0F1F35; "
        "}"
    );
    badge->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Preferred);
    center->addWidget(badge, 0, Qt::AlignHCenter);

    mBody = new QLabel(this);
    mBody->setObjectName("comingSoonBody");
    mBody->setText("Enabled when the backend module ships.");
    mBody->setAlignment(Qt::AlignHCenter);
    astra::setStyle(mBody, 
        "QLabel { "
        "color: #8FA3BF; "
        "font-size: 14px; "
        "}"
    );
    center->addWidget(mBody, 0, Qt::AlignHCenter);

    row->addLayout(center);
    row->addStretch();
    layout->addLayout(row);
    layout->addStretch();
}

void ComingSoonPage::setModuleName(const QString& name) {
    mTitle->setText(name);
}

}  // namespace astra
