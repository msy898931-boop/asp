#include "NavButton.h"
#include "theme/Theme.h"
#include "SvgIcon.h"
#include <QPainter>
#include <QPaintEvent>
#include <QResizeEvent>
#include <QEnterEvent>

namespace astra {

static const char* kBgActive     = "#162A44";
static const char* kBgHover      = "#12233B";
static const char* kAccent       = "#4A90D9";
static const char* kTextPrimary  = "#E8EEF5";
static const char* kTextSecondary= "#8FA3BF";
static const char* kTextTertiary = "#5A6B80";

NavButton::NavButton(const QString& label, const QString& iconPath,
                     bool comingSoon, QWidget* parent)
    : QWidget(parent)
    , mLabel(label)
    , mComingSoon(comingSoon)
{
    setFixedHeight(36);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    setCursor(Qt::ArrowCursor);  // default cursor, also for coming-soon rows

    if (!iconPath.isEmpty()) {
        mIcon = new SvgIcon(iconPath, 16, this);
        refreshIconColor();
    }
    refreshIconColor();
}

void NavButton::setActive(bool active) {
    if (mActive == active) return;
    mActive = active;
    refreshIconColor();
    update();
}

void NavButton::refreshIconColor() {
    if (!mIcon) return;
    if (mComingSoon && !mActive && !mHover) {
        mIcon->setColor(astra::uiColor(kTextTertiary));
    } else if (mActive || mHover) {
        mIcon->setColor(astra::uiColor(kTextPrimary));
    } else {
        mIcon->setColor(astra::uiColor(kTextSecondary));
    }
}

QColor NavButton::textColor() const {
    if (mComingSoon && !mActive && !mHover) return astra::uiColor(kTextTertiary);
    if (mActive || mHover) return astra::uiColor(kTextPrimary);
    return astra::uiColor(kTextSecondary);
}

void NavButton::paintEvent(QPaintEvent* /*event*/) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);

    // Background: active (surface-2) or hover
    QColor bg = mActive ? astra::uiColor(kBgActive)
                        : (mHover ? astra::uiColor(kBgHover) : QColor(0, 0, 0, 0));
    if (bg.alpha() > 0) {
        p.setPen(Qt::NoPen);
        p.setBrush(bg);
        p.drawRoundedRect(rect().adjusted(0, 0, -1, -1), 8, 8);
    }

    // Active: 3px accent-blue left border
    if (mActive) {
        p.setPen(Qt::NoPen);
        p.setBrush(astra::uiColor(kAccent));
        p.drawRoundedRect(QRect(0, 3, 3, height() - 6), 1.5, 1.5);
    }

    // Icon handled by the child SvgIcon widget.

    // Label
    const int textX = mIcon ? 34 : 14;
    p.setPen(textColor());
    QFont f = font();
    f.setPixelSize(13);
    f.setWeight(QFont::Medium);
    p.setFont(f);
    p.drawText(QRect(textX, 0, width() - textX - 8, height()),
               Qt::AlignVCenter | Qt::AlignLeft, mLabel);
}

void NavButton::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        emit clicked();
    }
    QWidget::mousePressEvent(event);
}

void NavButton::enterEvent(QEnterEvent* event) {
    mHover = true;
    refreshIconColor();
    update();
    QWidget::enterEvent(event);
}

void NavButton::leaveEvent(QEvent* event) {
    mHover = false;
    refreshIconColor();
    update();
    QWidget::leaveEvent(event);
}

void NavButton::resizeEvent(QResizeEvent* event) {
    if (mIcon) {
        mIcon->move(12, (height() - mIcon->height()) / 2);
    }
    QWidget::resizeEvent(event);
}

}  // namespace astra
