"""Self-review: score past advisories, then feed the lessons back in.

A language model is stateless, so it cannot "remember" a bad call by itself.
This module gives it a memory the only honest way available: it reads the
advisor's own JSONL of past calls, waits a fixed horizon, compares the stance
with what the price actually did, and writes a scored outcome. Those outcomes
become a short Arabic "lessons" block in the next prompt — so a mistake made
once is at least visible the next time, instead of silently repeated.

Boundaries: this never touches the validated ledger. It reads and writes only
the advisor's own files, and every number it produces is best-effort.
"""

from __future__ import annotations

import json
import os
import time

# A NEUTRAL call counts as right when the price barely moved over the horizon.
NEUTRAL_BAND_PCT = float(os.environ.get("ASTRA_ADVISOR_NEUTRAL_BAND", "0.2"))


def read_jsonl(path: str) -> list[dict]:
    """Every well-formed record in a JSONL file; a bad line is skipped."""
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return records


def append_jsonl(path: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # Best-effort; review must never break a response.


def advisory_key(record: dict) -> str:
    return "%s:%s" % (record.get("generated_at_utc"), record.get("symbol"))


def evaluate(advisory: dict, price_after: float) -> dict | None:
    """Was the stance right, given where the price ended up?"""
    price_at = advisory.get("price")
    stance = advisory.get("stance")
    if not price_at or price_after is None:
        return None
    move = (price_after - price_at) / price_at * 100.0
    if stance == "LONG":
        correct = move > 0
    elif stance == "SHORT":
        correct = move < 0
    else:
        correct = abs(move) <= NEUTRAL_BAND_PCT
    return {"move_pct": round(move, 3), "correct": bool(correct)}


def score_due(advisories: list[dict], outcomes: list[dict],
              price_now: float, now: float, horizon_s: float) -> list[dict]:
    """Outcome records for calls past the horizon and not yet scored."""
    scored = {advisory_key(o) for o in outcomes}
    new: list[dict] = []
    for adv in advisories:
        if adv.get("stance") not in ("LONG", "SHORT", "NEUTRAL"):
            continue
        key = advisory_key(adv)
        if key in scored:
            continue
        made_at = adv.get("generated_at_utc")
        if not isinstance(made_at, (int, float)) or now - made_at < horizon_s:
            continue
        result = evaluate(adv, price_now)
        if result is None:
            continue
        new.append({
            "generated_at_utc": made_at,
            "symbol": adv.get("symbol"),
            "stance": adv.get("stance"),
            "confidence": adv.get("confidence"),
            "news_used": adv.get("news_used"),
            "event": adv.get("event") or "",
            "price": adv.get("price"),
            "price_after": price_now,
            "scored_at_utc": int(now),
            **result,
        })
    return new


def summarize(outcomes: list[dict]) -> dict:
    total = len(outcomes)
    correct = sum(1 for o in outcomes if o.get("correct"))
    by_stance: dict[str, dict] = {}
    for o in outcomes:
        stance = o.get("stance", "NEUTRAL")
        bucket = by_stance.setdefault(stance, {"n": 0, "correct": 0})
        bucket["n"] += 1
        bucket["correct"] += 1 if o.get("correct") else 0
    recent_wrong = [o for o in outcomes if not o.get("correct")][-3:]
    return {
        "scored": total,
        "correct": correct,
        "hit_rate": round(correct / total, 3) if total else None,
        "by_stance": by_stance,
        "recent_wrong": recent_wrong,
    }


def lessons_block(summary: dict, lang: str = "ar") -> str:
    """A compact prompt block drawn from the advisor's own track record."""
    if not summary.get("scored"):
        return ""
    hit = summary.get("hit_rate") or 0.0
    if lang == "ar":
        lines = ["مراجعة ذاتية لنتائجك السابقة (تعلّم منها ولا تكرّر الخطأ):",
                 "• دقّة اتجاهاتك السابقة: %d من %d (%.0f%%)."
                 % (summary["correct"], summary["scored"], hit * 100)]
        for o in summary.get("recent_wrong", []):
            lines.append(
                "• خطأ سابق: قلت %s عند %s ثم صار السعر %s (تحرّك %.2f%%). "
                "لا تكرّر نفس الحكم بلا سبب جديد."
                % (o.get("stance"), o.get("price"), o.get("price_after"),
                   o.get("move_pct") or 0.0))
        return "\n".join(lines)
    lines = ["Self-review of your past calls (learn from them, do not repeat):",
             "• Past hit rate: %d of %d (%.0f%%)."
             % (summary["correct"], summary["scored"], hit * 100)]
    for o in summary.get("recent_wrong", []):
        lines.append("• Past miss: you said %s at %s, price then %s (%.2f%%). "
                     "Do not repeat that call without a new reason."
                     % (o.get("stance"), o.get("price"), o.get("price_after"),
                        o.get("move_pct") or 0.0))
    return "\n".join(lines)
