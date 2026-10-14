"""The bot's volatility features must equal the real C++ engine's.

`bridge/telegram_bot/volatility.py` recomputes volatilityRatio and atrRatio in
Python so the bot can score without the engine. This runs the REAL
`aura_feature_dump` (the T26 boundary) over a synthetic bar series and asserts
the Python values match to 1e-9, per timeframe.

If the engine's feature definitions change, regenerate and this test fails — the
same discipline as tests/models/test_engine_parity.py, but for the live path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from volatility import (  # noqa: E402
    CONTEXT_BARS,
    TRIGGER_WINDOW,
    timeframe_features,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DUMP = os.path.join(REPO, "build", "aura_feature_dump")
TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")


def _synth_m1(n_minutes: int, seed: int = 20210101):
    rows = []
    price = 1800.0
    start = 1735689600  # 2025-01-01 00:00:00 UTC
    for i in range(n_minutes):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        step = ((seed / 0x7FFFFFFF) - 0.5) * 0.9
        o = price
        c = round(price + step, 3)
        h = round(max(o, c) + abs(step) * 0.4, 3)
        l = round(min(o, c) - abs(step) * 0.4, 3)
        v = 10 + (seed % 90)
        rows.append((start + i * 60, o, h, l, c, v))
        price = c
    return rows


def _bucket(sec: int, tf_sec: int) -> int:
    return sec - (sec % tf_sec)


def _aggregate(m1, tf_sec: int):
    out = {}
    for sec, o, h, l, c, v in m1:
        b = _bucket(sec, tf_sec)
        cur = out.get(b)
        if cur is None:
            out[b] = [o, h, l, c, v]
        else:
            cur[1] = max(cur[1], h)
            cur[2] = min(cur[2], l)
            cur[3] = c
            cur[4] += v
    return sorted(out.items())


def _write_bars(path: str, bars) -> None:
    with open(path, "w", newline="") as fh:
        fh.write("timestamp_ms_utc,open,high,low,close,volume\n")
        for b, (o, h, l, c, v) in bars:
            fh.write(f"{b * 1000},{o},{h},{l},{c},{v}\n")


@unittest.skipUnless(os.path.exists(DUMP), "aura_feature_dump not built")
class VolatilityEngineParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.mkdtemp(prefix="vol_parity_")
        m1 = _synth_m1(60 * 24 * 250)  # 250 days -> every tf gets a 200-bar context
        tf_seconds = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
        cls.bars_by_tf = {}
        for tf, sec in tf_seconds.items():
            bars = _aggregate(m1, sec)
            cls.bars_by_tf[tf] = bars
            _write_bars(os.path.join(cls.workdir, f"{tf}.csv"), bars)
        # Decision = an M15 close well past the D1 context, so every timeframe
        # has a full 200-bar context window.
        m15 = cls.bars_by_tf["M15"]
        cls.as_of = m15[-1][0] + 900
        dec = os.path.join(cls.workdir, "decisions.csv")
        with open(dec, "w") as fh:
            fh.write(f"{cls.as_of}\n")
        out = os.path.join(cls.workdir, "out.json")
        subprocess.run(
            [DUMP, cls.workdir, dec, out, ",".join(TIMEFRAMES)],
            check=True, capture_output=True,
        )
        with open(out) as fh:
            cls.engine = json.load(fh)[0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def _engine_series(self, tf):
        """Bars closed at or before the decision instant, newest last."""
        sec = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}[tf]
        return [{"close": c, "high": h, "low": l}
                for b, (o, h, l, c, v) in self.bars_by_tf[tf]
                if b + sec <= self.as_of]

    def test_python_matches_engine_on_every_timeframe(self):
        vectors = {v["timeframe"]: v for v in self.engine["perTimeframe"]}
        for tf in TIMEFRAMES:
            self.assertTrue(vectors[tf]["valid"], f"{tf} not valid in engine")
            bars = self._engine_series(tf)
            self.assertGreaterEqual(len(bars), CONTEXT_BARS)
            feats = timeframe_features(bars)
            self.assertIsNotNone(feats)
            for name in ("volatilityRatio", "atrRatio"):
                engine_value = vectors[tf]["values"][name]
                self.assertAlmostEqual(
                    feats[name], engine_value, places=9,
                    msg=f"{tf}.{name}: python={feats[name]} engine={engine_value}",
                )

    def test_too_few_bars_yields_none_not_a_guess(self):
        self.assertIsNone(timeframe_features([]))
        self.assertIsNone(timeframe_features([{"close": 1, "high": 1, "low": 1}]
                                             * TRIGGER_WINDOW))


if __name__ == "__main__":
    unittest.main()
