"""A calibrated "will the next window be volatile?" model, for the bot.

Phase 0 (`research/horizon_probe.py`) showed the *sign* of the next move is not
predictable but its *size* is. This loads the artifact trained by
`scripts/volatility_lab.py` and scores the live market, so the bot can say
"stand aside — turbulence likely" instead of pretending to know direction.

Pure stdlib, no numpy: the model is a standardizer + logistic weights + a Platt
calibrator, all plain lists in the JSON. Features are recomputed here exactly as
the C++ engine defines them (sample stdev of log returns over the trigger
window, ratio against a 200-bar context), so the bot does not need the engine.
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Sequence

# Must match src/analysis/features/AnalyticalFeatures.h and AnalyticalFeatureEngine.h.
TRIGGER_WINDOW = 9
CONTEXT_BARS = 200
TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "vol_model.json")


def _logret_stdev(bars: Sequence[dict]) -> float:
    """Sample stdev of consecutive-close log returns (engine's logReturnStdev)."""
    rets = []
    for i in range(1, len(bars)):
        a, b = bars[i - 1].get("close"), bars[i].get("close")
        if a is None or b is None:
            continue
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if a > 0.0 and b > 0.0:
            rets.append(math.log(b / a))
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((v - mean) ** 2 for v in rets) / (len(rets) - 1)
    return math.sqrt(var)


def _mean_true_range(bars: Sequence[dict]) -> float:
    """Wilder's mean true range (engine's meanTrueRange)."""
    if len(bars) < 2:
        return 0.0
    total, count = 0.0, 0
    for i in range(1, len(bars)):
        try:
            high, low = float(bars[i]["high"]), float(bars[i]["low"])
            prev_close = float(bars[i - 1]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        total += tr
        count += 1
    return 0.0 if count == 0 else total / count


def _clamp_unit(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def timeframe_features(bars: Sequence[dict]) -> Optional[Dict[str, float]]:
    """volatilityRatio + atrRatio for one timeframe, or None if too few bars."""
    if len(bars) < TRIGGER_WINDOW + 1:
        return None
    window = bars[-TRIGGER_WINDOW:]
    context = bars[-CONTEXT_BARS:]
    short_vol = _logret_stdev(window)
    long_vol = _logret_stdev(context)
    vol_sum = short_vol + long_vol
    volatility_ratio = _clamp_unit(short_vol / vol_sum) if vol_sum > 0.0 else 0.5

    atr_window = _mean_true_range(window)
    atr_context = _mean_true_range(context)
    atr_sum = atr_window + atr_context
    atr_ratio = _clamp_unit(atr_window / atr_sum) if atr_sum > 0.0 else 0.5
    return {"volatilityRatio": volatility_ratio, "atrRatio": atr_ratio}


def _sigmoid(z: float) -> float:
    if z < -60.0:
        return 0.0
    if z > 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class VolatilityModel:
    """A loaded artifact: standardizer + logistic weights + Platt calibrator."""

    def __init__(self, artifact: dict) -> None:
        self.artifact = artifact
        self.columns: List[str] = list(artifact["columns"])
        self.means: List[float] = list(artifact["standardizer"]["means"])
        self.stds: List[float] = list(artifact["standardizer"]["stds"])
        self.weights: List[float] = list(artifact["weights"])
        self.intercept: float = float(artifact["intercept"])
        self.platt_a: float = float(artifact["calibrator"]["a"])
        self.platt_b: float = float(artifact["calibrator"]["b"])
        self.threshold: float = float(artifact.get("alert_threshold", 0.65))
        if not (len(self.columns) == len(self.means) == len(self.stds)
                == len(self.weights)):
            raise ValueError("volatility model artifact is inconsistent")

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> "VolatilityModel":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def _flat_row(self, bars_by_tf: Dict[str, Sequence[dict]]) -> Optional[Dict[str, float]]:
        row: Dict[str, float] = {}
        for tf in TIMEFRAMES:
            bars = bars_by_tf.get(tf)
            if not bars:
                continue
            feats = timeframe_features(bars)
            if feats is None:
                continue
            for name, value in feats.items():
                row[f"{tf}.{name}"] = value
        return row or None

    def predict(self, bars_by_tf: Dict[str, Sequence[dict]]) -> Optional[float]:
        """Calibrated probability that the next window is high-volatility.

        Returns None when no timeframe had enough bars to compute features, so
        the caller can stay silent rather than invent a number.
        """
        row = self._flat_row(bars_by_tf)
        if row is None:
            return None
        missing = [c for c in self.columns if c not in row]
        if missing:
            return None
        z = self.intercept
        for c, w, mean, std in zip(self.columns, self.weights, self.means,
                                   self.stds):
            z += w * ((row[c] - mean) / std)
        return _sigmoid(self.platt_a * z + self.platt_b)


def format_volatility_alert(probability: float, threshold: float) -> str:
    """Arabic alert text for a high-volatility reading."""
    pct = round(probability * 100)
    return (
        f"⚠️ تقلّب مرتفع متوقّع ({pct}%)\n"
        f"الحجم القادم يُرجَّح أن يكون أكبر من المعتاد. "
        f"الفلتر: {round(threshold * 100)}%+ — يُفضَّل عدم فتح صفقة ضيقة الآن.\n"
        f"(لا يتنبّأ بالاتجاه — فقط بحجم الحركة.)"
    )
