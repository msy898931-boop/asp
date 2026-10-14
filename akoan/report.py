#!/usr/bin/env python3
"""akoan report: the result as Arabic prose, and as plain JSON.

The report is written to the project's honesty rules:

  * It always **names its baseline** — the overall win rate the buckets are
    compared against — and never quotes a bucket rate without it.
  * It always states its **coverage** (how many events the ledger holds and
    over how many days), so a reader can see how thin the evidence is.
  * It never says a dimension "works". It says the data does or does not show a
    difference, and when it does not, it says that plainly.
  * It ends by saying what it is: decision support, not financial advice.

Both the Telegram text and the JSON are produced from the same result dict, so
they cannot disagree.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import engine as eng  # noqa: E402


def _pct(x) -> str:
    return "—" if x is None else "%.1f%%" % (100.0 * x)


def _r(x) -> str:
    return "—" if x is None else "%+.2fR" % x


def _dim_title(name: str) -> str:
    return eng.DIM_AR.get(name, name)


def _value_label(value: str) -> str:
    return eng.VALUE_AR.get(value, value)


def format_report(result: dict, coverage: dict | None = None) -> str:
    """The Arabic Telegram message for one akoan run."""
    v = result.get("verdict")
    head = {
        "EDGE_CANDIDATE": "🔎 akoan — مرشّح لحافة",
        "NO_EDGE_MEASURED": "🔎 akoan — ما انقاس تأثير للخبر",
        "INSUFFICIENT_DATA": "🔎 akoan — بيانات غير كافية",
    }.get(v, "🔎 akoan")

    lines = [head, ""]
    lines.append("السؤال: هل يفسّر الخبر نتائج الصفقات؟")
    lines.append("الصفقات المقيّمة: %d (ربح %d · خسارة %d) · صافي %s"
                 % (result.get("scored", 0), result.get("wins", 0),
                    result.get("losses", 0), _r(result.get("net_r"))))
    base = result.get("base_rate")
    if base is not None:
        lines.append("خط الأساس (معدّل الربح العام): %s — كل مقارنة تحته." % _pct(base))
    lines.append("")

    for dim in result.get("dimensions", []):
        rows = [b for b in dim.get("buckets", []) if b.get("n", 0) > 0]
        if not rows:
            continue
        lines.append("• %s" % _dim_title(dim["name"]))
        if not dim.get("comparable"):
            # One bucket only: there is no "other side" to compare against, so
            # the honest thing is to say the dimension could not be tested.
            b = rows[0]
            lines.append("   ما بينقاس: كل الصفقات بنفس الحالة (%s) — بلا مجموعة "
                         "مقارنة. %d صفقة مقيّمة."
                         % (_value_label(b["value"]), b.get("n", 0)))
            lines.append("")
            continue
        for b in rows:
            if b.get("win_rate") is None:
                lines.append("   %s: %d صفقة — %s"
                             % (_value_label(b["value"]), b["n"],
                                b.get("note") or "بيانات غير كافية"))
                continue
            mark = "⭐" if b.get("significant") else " "
            ci = b.get("ci_95")
            ci_txt = (" [%s–%s]" % (_pct(ci[0]), _pct(ci[1]))) if ci else ""
            edge = b.get("edge_vs_base")
            edge_txt = (" · فرق %s" % _pct(edge)) if edge is not None else ""
            lines.append("   %s %s: %s من %d%s%s · %s"
                         % (mark, _value_label(b["value"]), _pct(b.get("win_rate")),
                            b["n"], ci_txt, edge_txt, _r(b.get("net_r"))))
        pp = dim.get("permutation_p")
        if pp is not None:
            lines.append("   (اختبار تبديل على البُعد كامل: p=%.3f)" % pp)
        lines.append("")

    lines.append(result.get("verdict_ar", ""))
    if result.get("survivors"):
        lines.append("⭐ = نجا من تصحيح التعدّد (FDR q=%.2f)." % result.get("fdr_q", 0.10))
    else:
        lines.append("ولا بُعد نجا من تصحيح التعدّد — يعني الفروق تبدو عشوائية.")

    if coverage:
        lines.append("")
        lines.append("تغطية سجل الأخبار: %d حدث (%d صدر) خلال ~%d يوم."
                     % (coverage.get("events", 0), coverage.get("released", 0),
                        coverage.get("span_days", 0)))
        lines.append("⚠️ التغطية جزئية — النتيجة صحيحة بقدر ما في سجل، مو أكثر.")

    lines.append("")
    lines.append("محرك akoan — دعم قرار فقط، وليس نصيحة مالية.")
    return "\n".join(lines)


def compact(result: dict) -> dict:
    """The result minus the per-bucket prose, for logs and diffs."""
    return {
        "engine": result.get("engine"),
        "scored": result.get("scored"),
        "base_rate": result.get("base_rate"),
        "verdict": result.get("verdict"),
        "survivors": result.get("survivors"),
        "dimensions": [
            {"name": d["name"],
             "buckets": [{k: b.get(k) for k in
                          ("value", "n", "win_rate", "edge_vs_base",
                           "p_value", "significant")}
                         for b in d.get("buckets", [])]}
            for d in result.get("dimensions", [])
        ],
    }
