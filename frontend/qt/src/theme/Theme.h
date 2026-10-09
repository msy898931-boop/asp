#pragma once
//
// ASTRA — central colour translation for the two themes.
//
// Problem: the widget/pages paint with the dark palette baked into their
// setStyleSheet() strings and custom paintEvent()s. When the light theme is
// active those literals would render light-on-light (unreadable). Instead of
// touching every call site by hand, every stylesheet string is passed through
// astra::qss(), which rewrites the dark palette tokens to the matching light
// tokens when the light theme is active. Custom-painted widgets call
// astra::uiColor() for the same translation.
//
// The dark token set here is the single source of truth for those widgets.
// It matches the QSS files in src/resources/theme/.
//
#include <QColor>
#include <QApplication>
#include <QFrame>
#include <QString>
#include <QStringList>
#include <QWidget>

namespace astra {

inline bool isDarkTheme() {
    return qApp->property("astraDark").toBool();
}

// Map a dark-palette token to its light-theme equivalent. The light theme only
// defines a handful of "base" tokens (bg, surface, border, text tiers, accent);
// everything else is rounded to the nearest tier so the whole palette follows
// the theme instead of staying dark.
inline QString themeColor(const QString& hex, bool dark) {
    if (dark) return hex;
    const QString u = hex.toUpper();

    // ── base tokens: exact light equivalents ──
    if (u == "#0A1628") return QStringLiteral("#F5F7FA");  // app background
    if (u == "#0F1F35") return QStringLiteral("#FFFFFF");  // card surface
    if (u == "#12233B") return QStringLiteral("#EDF1F6");  // nav hover
    if (u == "#162A44" || u == "#24384F") return QStringLiteral("#E1E6ED");  // borders / surface-2
    if (u == "#E8EEF5") return QStringLiteral("#0A1628");  // text primary
    if (u == "#8FA3BF") return QStringLiteral("#5A6B80");  // text secondary
    if (u == "#5A6B80") return QStringLiteral("#8FA3BF");  // text tertiary
    if (u == "#4A90D9" || u == "#3A80C9") return QStringLiteral("#2E6BB8");  // accent

    // ── semantic colours: darkened a step for readable contrast on light ──
    if (u == "#4CAF7A") return QStringLiteral("#2E7D4F");  // green / ok
    if (u == "#D95A5A") return QStringLiteral("#B84A4A");  // red / error
    if (u == "#D9A14A") return QStringLiteral("#A87A32");  // amber / warn

    return hex;
}

// Translate every dark-palette hex literal inside a stylesheet string.
// Two-phase replacement: tokens are first swapped to unique placeholders so a
// replacement value can never be re-matched by a later token rule.
inline QString qss(const QString& sheet) {
    if (isDarkTheme()) return sheet;
    QString s = sheet;
    static const char* const kTokens[] = {
        "#0A1628", "#0F1F35", "#12233B", "#162A44", "#24384F",
        "#E8EEF5", "#8FA3BF", "#5A6B80",
        "#4A90D9", "#3A80C9",
        "#4CAF7A", "#D95A5A", "#D9A14A",
    };
    int idx = 0;
    QStringList placeholders;
    for (const char* tok : kTokens) {
        const QString from = QString::fromLatin1(tok);
        if (!s.contains(from, Qt::CaseInsensitive)) continue;
        const QString ph = QStringLiteral("\x01%1\x01").arg(idx++);
        s.replace(from, ph, Qt::CaseInsensitive);
        placeholders.append(themeColor(from, false));
    }
    for (int i = 0; i < placeholders.size(); ++i) {
        s.replace(QStringLiteral("\x01%1\x01").arg(i), placeholders[i]);
    }
    return s;
}

// Same translation for a colour used in a custom paintEvent()/setColor().
inline QColor uiColor(const QString& hex) {
    if (isDarkTheme()) return QColor(hex);
    return QColor(themeColor(hex, false));
}

// ─────────────────────────────────────────────────────────────────────────────
// Theme-aware stylesheet application.
//
// qss() alone only translates at call time, so a widget styled while the dark
// theme is active keeps its dark string after a switch to light. setStyle()
// also remembers the ORIGINAL (dark) string on the widget and re-applies the
// translated version for the current theme. reapplyTheme() walks the tree and
// re-applies every registered widget after the theme changes.
// ─────────────────────────────────────────────────────────────────────────────
inline const char* kRawSheetProp() { return "_astra_raw_qss"; }

inline void applyRegistered(QWidget* w) {
    const QString raw = w->property(kRawSheetProp()).toString();
    if (!raw.isNull()) w->setStyleSheet(qss(raw));
}

inline void setStyle(QWidget* w, const QString& sheet) {
    // A plain QWidget only paints a `background` declared in its own stylesheet
    // when WA_StyledBackground is set.
    if (w && qobject_cast<QWidget*>(w) && !qobject_cast<QFrame*>(w))
        w->setAttribute(Qt::WA_StyledBackground, true);
    w->setProperty(kRawSheetProp(), sheet);
    w->setStyleSheet(qss(sheet));
}

inline void reapplyTheme(QWidget* root) {
    if (!root) return;
    if (!root->property(kRawSheetProp()).toString().isNull())
        applyRegistered(root);
    const auto kids = root->findChildren<QWidget*>(QString(), Qt::FindDirectChildrenOnly);
    for (QWidget* w : kids) reapplyTheme(w);
    root->update();
}

}  // namespace astra
