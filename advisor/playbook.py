#!/usr/bin/env python3
"""The playbook: named trading schools, each with its measured record.

What this is, and what it deliberately is not:

  * It is a catalogue of **named strategies** ("schools"), each written down as
    an explicit, testable rule over facts the engine already has: the regime,
    the structure bias, RSI, ATR, the session, the higher-timeframe alignment.
    A school is a hypothesis about *when* the M15 directional read is worth
    taking, not a promise that it works.
  * Every school carries a **measured record** — the win rate, the net R and
    the trade count it actually produced when the rule was replayed over the
    real Dukascopy M15 book (2021-2025). That record is *not* produced here and
    is *not* produced by the model. It is produced by ``research/features_real``
    (the replay + ``validation_gate``) and pasted in below as a table, so the
    number a user reads is the number the project measured.
  * It is honest about the one finding that matters: **no school in the
    catalogue clears the promotion gate on its own.** The directional M15 read
    is close to a coin flip (OOS directional accuracy 0.517, which the project's
    own Phase-0 work showed is drift, not skill), and every filter built on it
    lands at or below break-even out-of-sample. That is why the playbook exists
    as *description*, not as an edge claim, and why the honest headline is
    "here is the school and here is its measured, unflattering record".

The point of the module is that the AI engine can now say **which school** a
trade belongs to and **what that school's measured record is**, instead of
quoting the model's own optimistic guess. A model's ``win_rate`` is a claim; a
school's ``measured`` block is a measurement. The engine keeps both, clearly
separated, and never lets the claim wear the measurement's clothes.

Run it standalone to print the catalogue:

    python3 advisor/playbook.py
"""

from __future__ import annotations

from typing import Callable, Optional

# --- the measured table ----------------------------------------------------
# Source: research/features_real/rule_report.txt and study_report.txt, produced
# by replaying the real Dukascopy M15 book (2021-2025, 7084 resolved trades)
# through tracker/replay.py and scoring with tracker/validation_gate.py.
#
# The baseline book, for reference: 36.2% win, R/trade -0.080 — i.e. the
# unselective M15 read loses. Break-even sits near 40% because the live ladder
# is 1.5*ATR stop to 2.5*ATR target (R:R 1.667), so a ~37.5% raw hit rate plus
# the spread is roughly where zero sits. Every school below is a hypothesis that
# some slice of the book beats that; the measured columns say whether it does.
#
# A school's numbers are copied verbatim from those reports. If the research is
# re-run and the numbers move, update this table in the same commit — the tests
# pin the shape (a measured block per school), not the values, precisely so the
# values can be corrected without rewriting tests.

BASELINE: dict = {
    "name": "baseline book",
    "n": 7084,
    "win_pct": 36.2,
    "r_per_trade": -0.080,
    "note": "the unselective M15 read: every trade the live rules open",
}

# The EMA-slope threshold the replay uses to call a regime trending, mirrored
# here so the engine can label a live trade with the same vocabulary the schools
# were measured in. Kept in one place; if the replay's threshold moves, move
# this with it (the playbook tests do not pin the value, deliberately).
TREND_SLOPE_THRESHOLD = 0.0008


class School:
    """One named strategy: when it fires, and what it measured.

    ``fires`` is a predicate over a context row (the flat dict ``features.extract``
    produces: regime, bias, direction, session, weekday, h4_align, rsi, atr_pct).
    ``measured`` is the record from the real replay, or ``None`` when the rule
    has not been scored yet — which the reader is told plainly rather than shown
    a zero that looks like a result.

    ``priority`` decides which label wins when several schools fire on one trade.
    The schools sit on independent axes (higher-timeframe alignment, regime,
    catalyst), so they are not nested and "last one in the list" would be an
    accident of ordering. A higher priority is the more specific, more
    informative label; ties fall to the earlier catalogue entry.
    """

    def __init__(self, key: str, name: str, school: str, thesis: str,
                 fires: Callable[[dict], bool],
                 measured: Optional[dict] = None, priority: int = 10,
                 name_ar: str = "", family_ar: str = "") -> None:
        self.key = key
        self.name = name
        self.name_ar = name_ar
        self.family_ar = family_ar
        self.school = school
        self.thesis = thesis
        self.fires = fires
        self.measured = measured
        self.priority = priority

    def matches(self, row: dict) -> bool:
        try:
            return bool(self.fires(row))
        except (KeyError, TypeError, ValueError):
            # A row missing a field simply does not match; a malformed row must
            # never raise into the engine's request path.
            return False

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "name_ar": self.name_ar,
            "school": self.school,
            "school_ar": self.family_ar,
            "thesis": self.thesis,
            "measured": self.measured,
            "clears_gate": bool(self.measured and self.measured.get("clears_gate")),
        }


# --- helpers over a context row -------------------------------------------

def _h4(row: dict) -> str:
    return str(row.get("h4_align") or "").upper()


def _regime(row: dict) -> str:
    return str(row.get("regime") or "").upper()


# --- the catalogue ---------------------------------------------------------
# Ordered from the broadest slice to the narrowest. Each thesis is one sentence
# a trader would actually say; each measured block is what the replay said back.

SCHOOLS: tuple[School, ...] = (
    School(
        key="trend_following",
        name="Trend following",
        name_ar="تتبّع الاتجاه",
        family_ar="الاتجاه",
        school="Trend",
        thesis="Trade only with the higher-timeframe trend; the H4 EMA cross "
               "and the M15 direction must agree, so the position rides the "
               "dominant flow rather than fighting it.",
        fires=lambda row: _h4(row) == "ALIGNED",
        measured={
            "source": "research/features_real/rule_report.txt",
            "scope": "all years — rule: H4-aligned only",
            "n": 3210,
            "win_pct": 36.9,
            "r_per_trade": -0.062,
            "oos_n": 1797,
            "oos_r_per_trade": -0.041,
            "permutation_p": 0.2105,
            "clears_gate": False,
            "why_not": "in-sample Wilson low 34.8% misses break-even 41.4%; "
                       "net R is negative in both halves",
        },
        priority=30,
    ),
    School(
        key="counter_trend",
        name="Counter-trend fade",
        name_ar="معاكسة الاتجاه",
        family_ar="الارتداد",
        school="Mean reversion",
        thesis="Fade the higher-timeframe trend when the M15 read turns: the "
               "move against H4 is the reversion trade, taken when the short "
               "frame rolls over.",
        fires=lambda row: _h4(row) == "AGAINST",
        measured={
            "source": "research/features_real/study_report.txt",
            "scope": "in-sample (split at 2024) — rule: H4 against",
            "n": 1460,
            "win_pct": 35.0,
            "r_per_trade": -0.152,
            "oos_n": 1438,
            "oos_win_pct": 36.2,
            "oos_r_per_trade": -0.066,
            "clears_gate": False,
            "why_not": "worse than the aligned slice; in-sample Wilson low 32.6% "
                       "misses break-even 41.6%, and both halves stay negative",
        },
        priority=30,
    ),
    School(
        key="news_momentum",
        name="Post-release momentum",
        name_ar="زخم ما بعد الخبر",
        family_ar="الحدث",
        school="Event-driven",
        thesis="Trade the window right after a high-impact release, when the "
               "first impulse is still in force and the spread has not yet "
               "eaten the move.",
        fires=lambda row: bool(row.get("recent_release")),
        measured={
            "source": "research/features_real/rule_report.txt",
            "scope": "all years — rule: post-news only (<=2h)",
            "n": 409,
            "win_pct": 40.1,
            "r_per_trade": 0.056,
            "oos_n": 281,
            "oos_win_pct": 38.9,
            "oos_r_per_trade": 0.043,
            "permutation_p": 0.0084,
            "clears_gate": False,
            "why_not": "the only slice with a positive R on both halves, but the "
                       "in-sample Wilson low 34.7% still misses break-even 39.9% "
                       "and the out-of-sample low misses 38.8% — promising, not proven",
        },
        priority=40,
    ),
    School(
        key="news_trend_confluence",
        name="Release + trend confluence",
        name_ar="تقاطع الخبر مع الاتجاه",
        family_ar="الحدث",
        school="Event-driven",
        thesis="The narrowest and best-looking slice: trade only when the H4 "
               "trend is aligned AND the entry sits within two hours of a "
               "release, so direction and catalyst point the same way.",
        fires=lambda row: _h4(row) == "ALIGNED" and bool(row.get("recent_release")),
        measured={
            "source": "research/features_real/rule_report.txt",
            "scope": "all years — rule: H4-aligned AND within 120 min after a release",
            "n": 208,
            "win_pct": 43.8,
            "r_per_trade": 0.117,
            "is_n": 140,
            "is_win_pct": 48.5,
            "is_r_per_trade": 0.188,
            "oos_n": 142,
            "oos_win_pct": 39.0,
            "oos_r_per_trade": 0.046,
            "permutation_p": 0.0067,
            "clears_gate": False,
            "why_not": "the strongest candidate in the book (in-sample R/trade "
                       "+0.188, permutation p=0.0067) but the in-sample Wilson "
                       "low 39.1% still misses break-even 39.9%, and the sample "
                       "is small — REJECT, revisit with more data",
        },
        priority=50,
    ),
    School(
        key="trend_regime",
        name="Regime-filtered trend",
        name_ar="اتجاه مُرشَّح بحالة السوق",
        family_ar="الاتجاه",
        school="Trend",
        thesis="Take the trend trade only in a confirmed trending regime, where "
               "the EMA slope is strong and agrees with structure, and stand "
               "aside in chop.",
        fires=lambda row: _regime(row) in ("TRENDING_UP", "TRENDING_DOWN"),
        measured={
            "source": "research/features_real/study_report.txt",
            "scope": "in-sample (split at 2024) — rule: regime TRENDING_UP/DOWN",
            "n": 1306,
            "win_pct": 35.9,
            "r_per_trade": -0.111,
            "oos_n": 1622,
            "oos_win_pct": 36.0,
            "oos_r_per_trade": -0.055,
            "clears_gate": False,
            "why_not": "the trend regimes do not separate from the book; both "
                       "halves stay below break-even",
        },
        priority=20,
    ),
)


def by_key(key: str) -> Optional[School]:
    for school in SCHOOLS:
        if school.key == key:
            return school
    return None


def match(row: dict) -> list[School]:
    """Every school whose rule fires on this context row, catalogue order."""
    return [s for s in SCHOOLS if s.matches(row)]


def primary(row: dict) -> Optional[School]:
    """The single best-fitting school for a row: the highest priority that fires.

    Several schools can fire on one trade because they sit on independent axes
    (higher-timeframe alignment, regime, catalyst). The label that wins is the
    most specific — the one carrying the most conditions — which is the school's
    ``priority``. Ties fall to the earlier catalogue entry. ``None`` when nothing
    fires, which is a real answer: the trade belongs to no named school.
    """
    hits = match(row)
    if not hits:
        return None
    return max(hits, key=lambda s: s.priority)


def catalogue() -> list[dict]:
    """The whole playbook as plain dicts, for an API response or a report."""
    return [s.as_dict() for s in SCHOOLS]


def clears_gate_count() -> int:
    return sum(1 for s in SCHOOLS if s.measured and s.measured.get("clears_gate"))


def _main() -> int:
    print("ASTRA playbook — %d named schools" % len(SCHOOLS))
    print("baseline: %s  n=%d  win=%.1f%%  R/trade=%+.3f"
          % (BASELINE["name"], BASELINE["n"], BASELINE["win_pct"],
             BASELINE["r_per_trade"]))
    print()
    for s in SCHOOLS:
        m = s.measured or {}
        verdict = "CLEARS GATE" if m.get("clears_gate") else "does not clear gate"
        print("[%s] %s (%s)" % (s.key, s.name, s.school))
        print("    thesis : %s" % s.thesis)
        if m:
            print("    measured: n=%d win=%.1f%% R/trade=%+.3f  OOS n=%d R/trade=%+.3f"
                  % (m["n"], m["win_pct"], m["r_per_trade"],
                     m.get("oos_n", 0), m.get("oos_r_per_trade", 0.0)))
            print("    verdict : %s — %s" % (verdict, m.get("why_not", "")))
        else:
            print("    measured: not scored yet")
        print()
    print("No school in this catalogue clears the promotion gate. That is the "
          "honest state of the research, not a defect of the catalogue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
