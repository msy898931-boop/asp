"""Deterministic tests for the probability output contract (DRAFT)."""

from __future__ import annotations

import json
import unittest

from src.models.api_contract import ProbabilityOutput, tier_for
from src.models.splits import SplitError


def record(**overrides):
    base = {
        "timestamp": "2025-06-01T12:00:00Z",
        "direction": "UP",
        "probability": 0.9,
        "calibrated": True,
        "confidence_interval": (0.8, 0.97),
        "coverage_tier": "high",
        "model_version": "v1.0",
    }
    base.update(overrides)
    return ProbabilityOutput(**base)


class TierTest(unittest.TestCase):
    def test_low_medium_high(self):
        self.assertEqual(tier_for(0.1), "low")
        self.assertEqual(tier_for(0.5), "medium")
        self.assertEqual(tier_for(0.9), "high")

    def test_boundaries(self):
        self.assertEqual(tier_for(0.0), "low")
        self.assertEqual(tier_for(1.0), "high")


class ContractValidationTest(unittest.TestCase):
    def test_valid_record_passes(self):
        record().validate()  # must not raise

    def test_direction_must_be_up_or_down(self):
        with self.assertRaises(SplitError):
            record(direction="SIDEWAYS").validate()

    def test_probability_range_enforced(self):
        with self.assertRaises(SplitError):
            record(probability=1.2, confidence_interval=(0.0, 1.0)).validate()

    def test_interval_must_bracket_probability(self):
        with self.assertRaises(SplitError):
            record(probability=0.9, confidence_interval=(0.95, 0.99)).validate()

    def test_tier_must_match_probability(self):
        with self.assertRaises(SplitError):
            record(probability=0.9, coverage_tier="low").validate()

    def test_model_version_required(self):
        with self.assertRaises(SplitError):
            record(model_version="").validate()

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(SplitError):
            record(timestamp="2025-06-01T12:00:00").validate()

    def test_bad_timestamp_rejected(self):
        with self.assertRaises(SplitError):
            record(timestamp="not-a-date").validate()


class SerializationTest(unittest.TestCase):
    def test_round_trip(self):
        original = record()
        restored = ProbabilityOutput.from_json(original.to_json())
        self.assertEqual(original, restored)

    def test_serialization_is_canonical(self):
        # Two records that differ only in tuple/list construction serialize the
        # same and keys are sorted, so output is byte-stable.
        a = record()
        b = record(confidence_interval=(0.8, 0.97))
        self.assertEqual(a.to_json(), b.to_json())
        self.assertEqual(list(json.loads(a.to_json()).keys()),
                         sorted(json.loads(a.to_json()).keys()))

    def test_uncalibrated_default_is_explicit(self):
        payload = json.loads(record(calibrated=False).to_json())
        self.assertFalse(payload["calibrated"])

    def test_from_json_rejects_bad_interval(self):
        bad = json.dumps(
            {
                "timestamp": "2025-06-01T12:00:00Z",
                "direction": "UP",
                "probability": 0.9,
                "calibrated": True,
                "confidence_interval": [0.8],
                "coverage_tier": "high",
                "model_version": "v1.0",
            }
        )
        with self.assertRaises(SplitError):
            ProbabilityOutput.from_json(bad)


if __name__ == "__main__":
    unittest.main()
