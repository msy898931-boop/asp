"""Deterministic tests for the feature-adapter boundary (harness prep).

These validate the contract between Agent-A's C++ feature layer and Agent-B's
model layer. They do NOT recompute features; they reject malformed or
out-of-range vectors so a contract mismatch fails loudly instead of silently
training on garbage.
"""

from __future__ import annotations

import json
import unittest

from src.models.features import (
    CROSS_BOUNDS,
    CROSS_DISCRETE,
    PER_TIMEFRAME_BOUNDS,
    PER_TIMEFRAME_DISCRETE,
    TIMEFRAMES,
    CrossFeatureVector,
    FeatureSet,
    FeatureVector,
    parse_feature_set_json,
)
from src.models.splits import SplitError


def per_values(**overrides):
    values = {name: 0.5 * (low + high) for name, (low, high) in PER_TIMEFRAME_BOUNDS.items()}
    for name in PER_TIMEFRAME_DISCRETE:
        values[name] = 0.0
    values.update(overrides)
    return values


def cross_values(**overrides):
    values = dict.fromkeys(CROSS_BOUNDS, 0.0)
    for name in CROSS_DISCRETE:
        values[name] = 0.0
    values.update(overrides)
    return values


ASOF = 1735689600


def tf(timeframe="M15", asof=ASOF, **overrides):
    return FeatureVector(
        timeframe=timeframe,
        asOfBarOpenSec=asof,
        values=per_values(**overrides),
        quality="VALID",
        valid=True,
    )


def cross(asof=ASOF):
    return CrossFeatureVector(
        asOfBarOpenSec=asof,
        values=cross_values(),
        quality="VALID",
        valid=True,
        m15Available=True,
        h4Available=True,
    )


class FeatureVectorTest(unittest.TestCase):
    def test_valid_vector_passes(self):
        tf().validate()

    def test_missing_feature_rejected(self):
        values = per_values()
        del values["structureTrend"]
        with self.assertRaises(SplitError):
            FeatureVector("M15", 0, values, "VALID", True).validate()

    def test_undeclared_feature_rejected(self):
        values = per_values()
        values["mysteryFeature"] = 0.1
        with self.assertRaises(SplitError):
            FeatureVector("M15", 0, values, "VALID", True).validate()

    def test_out_of_range_rejected(self):
        with self.assertRaises(SplitError):
            tf(structureTrend=1.5).validate()

    def test_unknown_timeframe_rejected(self):
        with self.assertRaises(SplitError):
            tf(timeframe="M7").validate()

    def test_unknown_quality_rejected(self):
        vector = FeatureVector("M15", 0, per_values(), "FINE", True)
        with self.assertRaises(SplitError):
            vector.validate()

    def test_discrete_must_be_minus_one_zero_or_one(self):
        with self.assertRaises(SplitError):
            tf(candleDirection=0.5).validate()

    def test_boundary_values_allowed(self):
        tf(structureTrend=1.0, rangePosition=0.0).validate()


class CrossVectorTest(unittest.TestCase):
    def test_valid_cross_passes(self):
        CrossFeatureVector(0, cross_values(), "VALID", True, True, True).validate()

    def test_discrete_cross_rejected_when_fractional(self):
        with self.assertRaises(SplitError):
            CrossFeatureVector(
                0, cross_values(h4M15Agreement=0.25), "VALID", True, True, True
            ).validate()


class FeatureSetTest(unittest.TestCase):
    def test_canonical_order_enforced(self):
        bad = FeatureSet(
            asOfBarOpenSec=0,
            perTimeframe=(tf("M15"), tf("M1")),
            cross=None,
            quality="VALID",
            valid=True,
        )
        with self.assertRaises(SplitError):
            bad.validate()

    def test_duplicate_timeframe_rejected(self):
        bad = FeatureSet(0, (tf("M15"), tf("M15")), None, "VALID", True)
        with self.assertRaises(SplitError):
            bad.validate()

    def test_lookup_by_timeframe(self):
        good = FeatureSet(ASOF, (tf("M1"), tf("M15")), cross(), "VALID", True)
        good.validate()
        self.assertEqual(good.timeframe("M15").timeframe, "M15")

    def test_flat_row_is_deterministic_and_sorted(self):
        good = FeatureSet(ASOF, (tf("M1"), tf("M15")), cross(), "VALID", True)
        row_a = good.as_flat_row()
        row_b = good.as_flat_row()
        self.assertEqual(row_a, row_b)
        self.assertEqual(list(row_a.keys()), sorted(row_a.keys()))
        self.assertIn("M15.structureTrend", row_a)

    def test_flat_row_ordering_is_canonical(self):
        # M1 keys must all precede M15 keys.
        good = FeatureSet(ASOF, (tf("M1"), tf("M15")), cross(), "VALID", True)
        keys = list(good.as_flat_row().keys())
        self.assertLess(keys.index("M1.structureTrend"), keys.index("M15.structureTrend"))


class CommonDecisionInstantTest(unittest.TestCase):
    """Regression guards for Agent-D T01 audit findings F1/F2.

    A multi-timeframe feature set must describe exactly one decision instant.
    These tests fail loudly if a vector's asOf drifts from the set's, which is
    the shape of the non-causal defect Agent-D found in Agent-A's engine.
    """

    def test_mismatched_timeframe_asof_rejected(self):
        bad = FeatureSet(
            ASOF,
            (tf("M1", asof=ASOF), tf("M15", asof=ASOF + 900)),
            None,
            "VALID",
            True,
        )
        with self.assertRaises(SplitError):
            bad.validate()

    def test_mismatched_cross_asof_rejected(self):
        bad = FeatureSet(ASOF, (tf("M1"), tf("M15")), cross(asof=ASOF + 1), "VALID", True)
        with self.assertRaises(SplitError):
            bad.validate()

    def test_all_streams_share_one_instant(self):
        # Nine streams, one decision bar -> valid.
        streams = tuple(tf(name, asof=ASOF) for name in TIMEFRAMES)
        good = FeatureSet(ASOF, streams, cross(asof=ASOF), "VALID", True)
        good.validate()
        self.assertEqual(len({v.asOfBarOpenSec for v in good.perTimeframe}), 1)


class ParseTest(unittest.TestCase):
    def _payload(self):
        return {
            "asOfBarOpenSec": 1735689600,
            "perTimeframe": [
                {
                    "timeframe": "M15",
                    "asOfBarOpenSec": 1735689600,
                    "values": per_values(),
                    "quality": "VALID",
                    "valid": True,
                }
            ],
            "cross": {
                "asOfBarOpenSec": 1735689600,
                "values": cross_values(),
                "quality": "VALID",
                "valid": True,
                "m15Available": True,
                "h4Available": True,
            },
            "quality": "VALID",
            "valid": True,
        }

    def test_round_trip_parse(self):
        parsed = parse_feature_set_json(json.dumps(self._payload()))
        self.assertEqual(parsed.perTimeframe[0].timeframe, "M15")
        self.assertTrue(parsed.cross.h4Available)

    def test_parse_rejects_out_of_range(self):
        payload = self._payload()
        payload["perTimeframe"][0]["values"]["structureTrend"] = 2.0
        with self.assertRaises(SplitError):
            parse_feature_set_json(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
