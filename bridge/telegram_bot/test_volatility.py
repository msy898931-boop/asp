"""Unit tests for the bot's volatility model (no C++ binary required)."""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from volatility import (
    DEFAULT_PATH,
    TRIGGER_WINDOW,
    VolatilityModel,
    format_volatility_alert,
    timeframe_features,
)


def _bars(closes, spread=1.0):
    out = []
    for _i, c in enumerate(closes):
        out.append({"close": c, "high": c + spread, "low": c - spread})
    return out


class FeatureMathTest(unittest.TestCase):
    def test_flat_series_has_neutral_ratios(self):
        # A constant price -> zero volatility everywhere -> both ratios fall
        # back to 0.5 (the engine's degenerate-input convention).
        feats = timeframe_features(_bars([1800.0] * 250))
        self.assertEqual(feats["volatilityRatio"], 0.5)
        self.assertEqual(feats["atrRatio"], 0.5)

    def test_ratios_are_bounded_unit(self):
        closes = [1800.0 + (i % 7) for i in range(250)]
        feats = timeframe_features(_bars(closes))
        for name in ("volatilityRatio", "atrRatio"):
            self.assertGreaterEqual(feats[name], 0.0)
            self.assertLessEqual(feats[name], 1.0)

    def test_short_recent_move_raises_ratio_above_half(self):
        # Calm history, then a sharp burst in the trigger window: short vol
        # should dominate, pushing volatilityRatio above 0.5.
        calm = [1800.0 + 0.001 * i for i in range(241)]
        burst = [calm[-1] + (50 if i % 2 else -50) for i in range(1, 10)]
        feats = timeframe_features(_bars(calm + burst))
        self.assertGreater(feats["volatilityRatio"], 0.5)

    def test_too_few_bars_returns_none(self):
        self.assertIsNone(timeframe_features(_bars([1800.0] * TRIGGER_WINDOW)))


class ModelLoadTest(unittest.TestCase):
    def test_shipped_artifact_loads_and_is_consistent(self):
        model = VolatilityModel.load(DEFAULT_PATH)
        self.assertGreater(len(model.columns), 0)
        self.assertEqual(len(model.columns), len(model.weights))

    def test_predict_is_a_probability(self):
        model = VolatilityModel.load(DEFAULT_PATH)
        closes = [1800.0 + math.sin(i / 5.0) * 8 for i in range(260)]
        bars_by_tf = {tf: _bars(closes) for tf in
                      ("M5", "M15", "H1", "H4", "D1")}
        p = model.predict(bars_by_tf)
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_predict_is_deterministic(self):
        model = VolatilityModel.load(DEFAULT_PATH)
        closes = [1800.0 + math.sin(i / 3.0) * 5 for i in range(260)]
        bars_by_tf = {tf: _bars(closes) for tf in
                      ("M5", "M15", "H1", "H4", "D1")}
        self.assertEqual(model.predict(bars_by_tf), model.predict(bars_by_tf))

    def test_missing_timeframes_yield_none_not_a_guess(self):
        model = VolatilityModel.load(DEFAULT_PATH)
        self.assertIsNone(model.predict({"M15": _bars([1800.0] * 260)}))

    def test_inconsistent_artifact_is_rejected(self):
        bad = {"columns": ["a.b"], "standardizer": {"means": [0.0], "stds": [1.0]},
               "weights": [], "intercept": 0.0,
               "calibrator": {"a": 1.0, "b": 0.0}}
        with self.assertRaises(ValueError):
            VolatilityModel(bad)


class AlertTextTest(unittest.TestCase):
    def test_alert_states_it_does_not_predict_direction(self):
        text = format_volatility_alert(0.72, 0.65)
        self.assertIn("72%", text)
        self.assertIn("لا يتنبّأ بالاتجاه", text)


if __name__ == "__main__":
    unittest.main()
