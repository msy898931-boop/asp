"""Causal, labeled dataset assembly for the model layer (T03 support).

Turns a chronological sequence of feature snapshots plus the aligned close
prices into labeled examples, then partitions them without leaking a label
window across a partition boundary.

Causality and leakage rules enforced here:
  * Features at instant i use only bars at or before i (guaranteed upstream by
    Agent-A's engine and validated by `features.py`).
  * The label uses the FUTURE price (that is the target) but is only ever read
    from the partition it belongs to. A row is PURGED from a partition when its
    label window would reach into the next partition, so no training row can be
    scored by validation/OOS information (and vice versa).
  * Everything is a pure function of the input; no shuffling, no randomness.

This module contains no modelling and publishes no probability.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import List, Mapping, Sequence, Tuple

from src.models.features import FeatureSet
from src.models.splits import Split, SplitError, chronological_split


@dataclass(frozen=True)
class LabeledExample:
    """One training row: features at a decision instant, plus its forward label."""

    timestamp: int          # asOfBarOpenSec of the decision instant
    features: Mapping[str, float]
    label: int              # 1 = price rose over the horizon, 0 = fell/flat
    label_timestamp: int    # asOfBarOpenSec of the bar the label is read from

    def validate(self) -> None:
        if self.label not in (0, 1):
            raise SplitError(f"label must be 0 or 1, got {self.label!r}")
        if self.label_timestamp <= self.timestamp:
            raise SplitError(
                f"label timestamp {self.label_timestamp} must be after decision "
                f"instant {self.timestamp}"
            )


def build_labeled_examples(
    feature_sets: Sequence[FeatureSet],
    closes: Sequence[float],
    horizon: int = 1,
) -> List[LabeledExample]:
    """Pair each feature snapshot with the forward price move over `horizon` bars.

    `closes[i]` is the close observed at `feature_sets[i].asOfBarOpenSec`. The
    last `horizon` snapshots have no forward bar and are dropped (they cannot be
    labeled without looking past the data).

    Requires the snapshots to be strictly increasing in time.
    """
    if horizon < 1:
        raise SplitError("horizon must be >= 1")
    if len(feature_sets) != len(closes):
        raise SplitError(
            f"length mismatch: {len(feature_sets)} feature sets vs "
            f"{len(closes)} closes"
        )
    if len(feature_sets) <= horizon:
        raise SplitError(
            f"{len(feature_sets)} snapshots is too few for horizon={horizon}"
        )

    stamps = [fs.asOfBarOpenSec for fs in feature_sets]
    for previous, current in itertools.pairwise(stamps):
        if current <= previous:
            raise SplitError(
                "feature snapshots must be strictly increasing in time; "
                f"got {previous} then {current}"
            )

    examples: List[LabeledExample] = []
    for i in range(len(feature_sets) - horizon):
        future = i + horizon
        label = 1 if closes[future] > closes[i] else 0
        example = LabeledExample(
            timestamp=stamps[i],
            features=feature_sets[i].as_flat_row(),
            label=label,
            label_timestamp=stamps[future],
        )
        example.validate()
        examples.append(example)
    return examples


def feature_columns(examples: Sequence[LabeledExample]) -> Tuple[str, ...]:
    """The canonical, sorted feature-column order. Every example must carry the
    identical column set, so the training matrix is well-defined and stable."""
    if not examples:
        raise SplitError("no examples")
    first = tuple(sorted(examples[0].features))
    for example in examples[1:]:
        if tuple(sorted(example.features)) != first:
            raise SplitError(
                f"inconsistent feature columns at instant {example.timestamp}"
            )
    return first


def to_matrix(
    examples: Sequence[LabeledExample], columns: Sequence[str]
) -> Tuple[List[List[float]], List[int]]:
    """Dense (X, y) in `columns` order. Pure and deterministic."""
    x = [[float(example.features[name]) for name in columns] for example in examples]
    y = [example.label for example in examples]
    return x, y


@dataclass(frozen=True)
class PurgedSplit:
    """A chronological split with label-window overlap removed at the seams."""

    development: List[LabeledExample]
    validation: List[LabeledExample]
    oos: List[LabeledExample]

    def as_dict(self):
        return {
            "development": list(self.development),
            "validation": list(self.validation),
            "oos": list(self.oos),
        }


def assert_partitions_separated(named_partitions) -> None:
    """Structural guard: named partitions must be pairwise disjoint by timestamp
    and supplied in strictly increasing chronological order.

    `named_partitions` is a sequence of `(name, rows)`. Empty partitions are
    skipped. Raises `SplitError` on overlap or inversion.

    This is what makes "fit on one partition, score another" trustworthy: a
    caller who accidentally passes the same rows twice, or passes a later
    partition before an earlier one, is rejected rather than handed a
    tautological near-zero error. Callers that want the check to be real must
    invoke it — the runners below do.
    """
    seen: dict = {}
    previous_max = None
    previous_name = None
    for name, rows in named_partitions:
        if not rows:
            continue
        stamps = [row.timestamp for row in rows]
        if stamps != sorted(stamps):
            raise SplitError(f"partition '{name}' is not in chronological order")
        for ts in stamps:
            owner = seen.get(ts)
            if owner is not None:
                raise SplitError(
                    f"timestamp {ts} appears in both '{owner}' and '{name}'"
                )
            seen[ts] = name
        if previous_max is not None and stamps[0] <= previous_max:
            raise SplitError(
                f"partition '{name}' does not start after '{previous_name}' "
                f"(first {stamps[0]} <= {previous_max})"
            )
        previous_max = stamps[-1]
        previous_name = name


def purge_split(
    examples: Sequence[LabeledExample],
    development_years: Sequence[int] | None = None,
    validation_years: Sequence[int] | None = None,
    oos_years: Sequence[int] | None = None,
) -> PurgedSplit:
    """Chronological split, then drop seam rows whose label window crosses into
    the next partition.

    A development row is purged if its `label_timestamp` is at or after the
    first validation row's timestamp; likewise validation vs OOS. This is the
    standard embargo that keeps a partition's labels inside that partition.
    """
    # Reuse the year-based chronological split by adapting examples to Samples.
    from datetime import datetime, timezone

    from src.models.splits import Sample

    samples = [
        Sample(timestamp=datetime.fromtimestamp(e.timestamp, tz=timezone.utc), payload=e)
        for e in examples
    ]
    kwargs = {}
    if development_years is not None:
        kwargs["development_years"] = development_years
    if validation_years is not None:
        kwargs["validation_years"] = validation_years
    if oos_years is not None:
        kwargs["oos_years"] = oos_years
    split: Split = chronological_split(samples, **kwargs)

    def unwrap(rows):
        return [s.payload for s in rows]

    dev = unwrap(split.development)
    val = unwrap(split.validation)
    oos = unwrap(split.oos)

    def embargo(head: List[LabeledExample], tail: List[LabeledExample]):
        if not head or not tail:
            return head
        boundary = tail[0].timestamp
        return [e for e in head if e.label_timestamp < boundary]

    dev = embargo(dev, val)
    val = embargo(val, oos)
    return PurgedSplit(development=dev, validation=val, oos=oos)
