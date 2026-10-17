"""T27 — real-data calibration runner (E05 closer).

Feeds Agent-A's frozen `FeatureSet` JSON (plus the M15 close per instant) through
the T05 calibrated pipeline and reports the RULE C verdict. This is the same
measurement the synthetic demos perform, but on real features; the difference is
the *decision*, not the code path.

Data contract
-------------
The corpus is a directory of JSON files. Each file is either one `FeatureSet`
object (the interchange shape `src/models/features.py:parse_feature_set`
validates) or an array of them. Each row also carries its decision-bar close as a
**top-level sibling key** — by default ``close`` — because the label needs the
forward price move and labels are a *model-side* concern, never recomputed in
Agent-A's feature zone. (`parse_feature_set` reads only the FeatureSet fields, so
the sibling is ignored by the feature contract.) A row missing its close is an
error, not interpolation (data-phase rule: no silent interpolation).

The path is fully configurable (T28): pass ``corpus_dir`` / ``--corpus`` and
``close_key`` / ``--close-key``. Nothing here hardcodes a data location.

Honesty (RULE C / D / E)
------------------------
* RULE C — the calibration numbers are always reported. The *verdict* is a
  separate, explicit decision: publish as a probability only if OOS ECE < 0.05;
  0.05..0.10 publishes as a labelled score; > 0.10 reports the numbers and
  recommends a pivot. The runner never dresses a failure up as a probability.
* RULE D — per-tier coverage is reported next to accuracy; the tier mix is
  visible, never hidden. Empty partitions/tiers are stated, not fabricated.
* RULE E — a negative result is recorded (the report carries the numbers and the
  verdict even when the verdict is a failure).

Nothing here publishes a probability to the API surface. `/analysis/latest` stays
uncalibrated (E05) until the T29 audit signs off.
"""

from __future__ import annotations

import argparse
import gzip
import itertools
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple, cast

from src.models.baseline import PartitionMetrics
from src.models.calibrated import CalibratedReport, run_calibrated
from src.models.calibration import (
    ECE_FAILURE,
    ECE_TARGET,
    BinStats,
    DirectionBreakdown,
    TierCoverage,
    calibration_report,
)
from src.models.calibrators import fit_calibrator
from src.models.dataset import (
    LabeledExample,
    assert_partitions_separated,
    build_labeled_examples,
    feature_columns,
    to_matrix,
)
from src.models.features import FeatureSet, parse_feature_set
from src.models.logistic import fit_logistic
from src.models.splits import (
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    SplitError,
    assert_causal,
    chronological_split,
    fractional_split,
)
from src.models.walk_forward import (
    WalkForwardConfig,
    assert_no_leakage,
    test_segments_overlap,
    walk_forward,
)

DEFAULT_CLOSE_KEY = "close"

# T28 alignment: AURA_FEATURES_DIR is the canonical feature-corpus location;
# ASTRA_FEATURE_CORPUS is honoured as the T27 spelling. Default is the repo's
# research/features_real, computed from this file (never the CWD).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_FEATURES_DIR = os.path.join(_REPO_ROOT, "research", "features_real")
FEATURES_DIR_ENV = ("AURA_FEATURES_DIR", "ASTRA_FEATURE_CORPUS")
ENV_CLOSE_KEY = "AURA_CLOSE_KEY"


def resolve_corpus_dir(explicit: str = "", environ=None) -> str:
    """Resolve the corpus directory: explicit > AURA_FEATURES_DIR > alias > repo default."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ if environ is None else environ
    for name in FEATURES_DIR_ENV:
        if env.get(name):
            return os.path.abspath(os.path.expanduser(env[name]))
    return DEFAULT_FEATURES_DIR


# RULE C verdicts.
PUBLISH_PROBABILITY = "probability"
PUBLISH_SCORE = "score"
REPORT_AND_PIVOT = "report_and_pivot"
CANNOT_PUBLISH = "cannot_publish"


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------


def _iter_json_files(corpus_dir: str) -> List[str]:
    paths: List[str] = []
    for root, _dirs, files in os.walk(corpus_dir):
        for name in sorted(files):
            if name.endswith(".json") or name.endswith(".json.gz"):
                paths.append(os.path.join(root, name))
    return sorted(paths)


def _coerce_rows(doc) -> List[Dict]:
    if isinstance(doc, list):
        return list(doc)
    return [doc]


def load_corpus(
    corpus_dir: str, close_key: str = DEFAULT_CLOSE_KEY
) -> Tuple[List[FeatureSet], List[float]]:
    """Load a corpus from a directory or a single file into ordered data.

    `corpus_dir` may be a directory of `*.json` / `*.json.gz` documents, or a
    single such file. Each document is a FeatureSet object (or an array of them)
    carrying its decision-bar close as a top-level sibling key (`close_key`).
    Raises `SplitError` on a missing path, an empty corpus, a missing/non-finite
    close, or duplicate instants. Never interpolates.
    """
    if os.path.isfile(corpus_dir):
        paths = [corpus_dir]
    elif os.path.isdir(corpus_dir):
        paths = _iter_json_files(corpus_dir)
    else:
        raise SplitError(f"corpus path does not exist: {corpus_dir}")
    if not paths:
        raise SplitError(f"no .json feature files under {corpus_dir}")
    gz_paths = [p for p in paths if p.endswith(".gz")]
    if gz_paths and len(paths) > 1:
        # A directory that contains a compressed corpus is a single-artifact
        # location; a second document there would silently concatenate different
        # providers/eras (MT5 + Dukascopy) or the same corpus twice (Agent-D F2).
        # Plain-multi-`.json` directories remain the legacy "parts" mode.
        names = ", ".join(os.path.basename(p) for p in paths)
        raise SplitError(
            f"corpus directory {corpus_dir} holds {len(paths)} corpus files "
            f"({names}); a directory with a .gz corpus must hold exactly one - "
            f"pass one explicit file (--corpus FILE)"
        )

    rows: List[Tuple[int, FeatureSet, float]] = []
    for path in paths:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                doc = json.load(handle)
        else:
            with open(path, "r", encoding="utf-8") as handle:
                doc = json.load(handle)
        for entry in _coerce_rows(doc):
            if not isinstance(entry, dict):
                raise SplitError(f"{path}: feature entry is not an object")
            if close_key not in entry:
                raise SplitError(
                    f"{path}: feature row {entry.get('asOfBarOpenSec')!r} is "
                    f"missing its decision-bar close under '{close_key}' "
                    "(no interpolation)"
                )
            close = entry[close_key]
            if not isinstance(close, (int, float)) or isinstance(close, bool):
                raise SplitError(f"{path}: close must be numeric, got {close!r}")
            close = float(close)
            if not math.isfinite(close):
                raise SplitError(
                    f"{path}: non-finite close for "
                    f"{entry.get('asOfBarOpenSec')!r}: {close!r}"
                )
            fs = parse_feature_set(entry)
            rows.append((fs.asOfBarOpenSec, fs, close))

    rows.sort(key=lambda item: item[0])
    for (t0, _, _), (t1, _, _) in itertools.pairwise(rows):
        if t1 == t0:
            raise SplitError(f"duplicate decision instant in corpus: {t0}")

    feature_sets = [row[1] for row in rows]
    closes = [row[2] for row in rows]
    return feature_sets, closes


# --------------------------------------------------------------------------
# RULE C verdict
# --------------------------------------------------------------------------


def rule_c_verdict(ece: float) -> Tuple[str, str]:
    """Map an OOS ECE to (verdict, human note). Strict boundaries per MISSION."""
    if ece < ECE_TARGET:
        return (
            PUBLISH_PROBABILITY,
            f"ECE {ece:.4f} < {ECE_TARGET}: calibration is within target; the "
            "value may be published as a probability once the T29 audit passes.",
        )
    if ece <= ECE_FAILURE:
        return (
            PUBLISH_SCORE,
            f"ECE {ece:.4f} in [{ECE_TARGET}, {ECE_FAILURE}]: publish as a "
            "'score', explicitly labelled — not as a probability.",
        )
    return (
        REPORT_AND_PIVOT,
        f"ECE {ece:.4f} > {ECE_FAILURE}: calibration does not hold; report the "
        "numbers and recommend a pivot. Do not present as a probability.",
    )


# --------------------------------------------------------------------------
# Walk-forward calibration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldCalibration:
    index: int
    train_size: int
    test_size: int
    test_start: int          # asOfBarOpenSec of the first test instance
    test_end: int            # asOfBarOpenSec of the last test instance
    brier: float
    ece: float
    mce: float
    accuracy: float
    n_test: int


@dataclass(frozen=True)
class WalkForwardSummary:
    folds: Tuple[FoldCalibration, ...]
    pooled_brier: float
    pooled_ece: float
    pooled_mce: float
    pooled_accuracy: float
    n_pooled: int
    overlap: bool
    verdict: str
    note: str

    def to_dict(self) -> dict:
        return {
            "folds": [asdict(f) for f in self.folds],
            "pooled_brier": self.pooled_brier,
            "pooled_ece": self.pooled_ece,
            "pooled_mce": self.pooled_mce,
            "pooled_accuracy": self.pooled_accuracy,
            "n_pooled": self.n_pooled,
            "overlap": self.overlap,
            "verdict": self.verdict,
            "note": self.note,
        }

    def summary(self) -> str:
        lines = [
            f"  walk-forward: {len(self.folds)} folds, pooled n={self.n_pooled} "
            f"(overlapping test windows: {self.overlap})",
            f"    pooled: brier={self.pooled_brier:.4f} ece={self.pooled_ece:.4f} "
            f"mce={self.pooled_mce:.4f} accuracy={self.pooled_accuracy:.4f}",
            f"    VERDICT: {self.verdict} — {self.note}",
        ]
        for f in self.folds:
            lines.append(
                f"      fold {f.index}: train={f.train_size} test={f.test_size} "
                f"brier={f.brier:.4f} ece={f.ece:.4f}"
            )
        return "\n".join(lines)


def _fit_calibrate_eval(train, test, method, model_factory, calibrator_fraction=0.5):
    """Fit base on the train's first part, calibrator on its second, score test.

    Keeps the calibrator disjoint from the scored test rows (T05 discipline) while
    never looking at the test window during fitting. Returns (probs, outcomes).
    """
    if not train or not test:
        raise SplitError("walk-forward fold has an empty train or test slice")
    cut = int(len(train) * calibrator_fraction)
    cut = max(1, min(cut, len(train) - 1))
    base_rows = train[:cut]
    cal_rows = train[cut:]
    assert_partitions_separated((("base", base_rows), ("cal", cal_rows), ("test", test)))

    columns = feature_columns(base_rows)
    for name, part in (("cal", cal_rows), ("test", test)):
        if feature_columns(part) != columns:
            raise SplitError(f"walk-forward '{name}' has different feature columns")

    x_base, y_base = to_matrix(base_rows, columns)
    model = fit_logistic(x_base, y_base) if model_factory is None else model_factory(x_base, y_base)

    x_cal, y_cal = to_matrix(cal_rows, columns)
    calibrator = fit_calibrator(method, [model.decision_function(r) for r in x_cal], y_cal)

    x_test, y_test = to_matrix(test, columns)
    probs = calibrator.transform_batch([model.decision_function(r) for r in x_test])
    return probs, y_test


def walk_forward_calibration(
    feature_sets: Sequence[FeatureSet],
    closes: Sequence[float],
    *,
    label_horizon: int = 1,
    method: str = "platt",
    model_factory: Optional[Callable] = None,
    train_size: int = 60,
    test_size: int = 20,
    step: int = 0,
) -> WalkForwardSummary:
    """Rolling-origin calibration over the whole real corpus (no year partitions).

    Each fold fits on a fixed train window and is scored once on the following
    test window; folds advance by `step` (default = test_size, non-overlapping
    test segments). Pooled OOS ECE drives the RULE C verdict.
    """
    examples = _to_examples(feature_sets, closes, label_horizon)
    samples = [
        Sample(datetime.fromtimestamp(ex.timestamp, tz=timezone.utc), ex)
        for ex in examples
    ]
    folds = walk_forward(samples, WalkForwardConfig(train_size, test_size, step))
    assert_no_leakage(folds)
    overlap = test_segments_overlap(folds)

    fold_results: List[FoldCalibration] = []
    pooled_probs: List[float] = []
    pooled_outcomes: List[int] = []
    for fold in folds:
        train = [cast(LabeledExample, s.payload) for s in fold.train]
        test = [cast(LabeledExample, s.payload) for s in fold.test]
        probs, outcomes = _fit_calibrate_eval(train, test, method, model_factory)
        rep = calibration_report(probs, outcomes)
        fold_results.append(
            FoldCalibration(
                index=fold.index,
                train_size=fold.train_size,
                test_size=fold.test_size,
                test_start=test[0].timestamp,
                test_end=test[-1].timestamp,
                brier=rep.brier,
                ece=rep.ece,
                mce=rep.mce,
                accuracy=sum(1 for p, y in zip(probs, outcomes) if (p >= 0.5) == bool(y))
                / len(test),
                n_test=len(test),
            )
        )
        pooled_probs.extend(probs)
        pooled_outcomes.extend(outcomes)

    pooled = calibration_report(pooled_probs, pooled_outcomes)
    verdict, note = rule_c_verdict(pooled.ece)
    if overlap:
        note += " (overlapping test windows — folds are not independent.)"

    return WalkForwardSummary(
        folds=tuple(fold_results),
        pooled_brier=pooled.brier,
        pooled_ece=pooled.ece,
        pooled_mce=pooled.mce,
        pooled_accuracy=sum(
            1 for p, y in zip(pooled_probs, pooled_outcomes) if (p >= 0.5) == bool(y)
        )
        / len(pooled_probs),
        n_pooled=len(pooled_probs),
        overlap=overlap,
        verdict=verdict,
        note=note,
    )


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RealCalibrationReport:
    corpus_dir: str
    close_key: str
    label_horizon: int
    method: str
    n_instants: int
    n_examples: int
    partition_sizes: Dict[str, int]
    raw_oos: Optional[PartitionMetrics]
    calibrated_oos: Optional[PartitionMetrics]
    coverage: Tuple[TierCoverage, ...]
    verdict: str
    note: str
    notes: Tuple[str, ...] = field(default_factory=tuple)
    walk_forward: Optional[WalkForwardSummary] = None
    # Directive labelling: a single-window (fraction) run is PROOF-OF-CONCEPT and
    # must NOT be sold as out-of-sample. Empty for a multi-year (year) run.
    publication_label: str = ""
    # F-T27-1 (Agent-D/solo): set-validity disclosure. `valid=false` sets are
    # INCOMPLETE warm-up rows; a report that trains on them must say so.
    n_valid_sets: int = 0
    n_invalid_sets: int = 0
    valid_only: bool = False

    @property
    def ece_oos(self) -> Optional[float]:
        return self.calibrated_oos.calibration.ece if self.calibrated_oos else None

    @property
    def brier_oos(self) -> Optional[float]:
        return self.calibrated_oos.calibration.brier if self.calibrated_oos else None

    @property
    def brier_skill_oos(self) -> Optional[float]:
        return self.calibrated_oos.calibration.brier_skill if self.calibrated_oos else None

    @property
    def directions(self) -> List[DirectionBreakdown]:
        return list(self.calibrated_oos.calibration.directions) if self.calibrated_oos else []

    @property
    def threshold_coverage(self) -> List[TierCoverage]:
        return (
            list(self.calibrated_oos.calibration.threshold_coverage)
            if self.calibrated_oos
            else []
        )

    @property
    def reliability_diagram(self) -> List[BinStats]:
        return list(self.calibrated_oos.calibration.diagram) if self.calibrated_oos else []

    def to_dict(self) -> dict:
        def metric(m: Optional[PartitionMetrics]):
            if m is None:
                return None
            return {
                "name": m.name,
                "n": m.n,
                "accuracy": m.accuracy,
                "brier": m.calibration.brier,
                "brier_skill": m.calibration.brier_skill,
                "ece": m.calibration.ece,
                "mce": m.calibration.mce,
                "meets_target": m.calibration.meets_target(),
                "is_failure": m.calibration.is_failure(),
            }

        cal = self.calibrated_oos.calibration if self.calibrated_oos else None
        return {
            "corpus_dir": self.corpus_dir,
            "close_key": self.close_key,
            "label_horizon": self.label_horizon,
            "method": self.method,
            "publication_label": self.publication_label,
            "n_valid_sets": self.n_valid_sets,
            "n_invalid_sets": self.n_invalid_sets,
            "valid_only": self.valid_only,
            "n_instants": self.n_instants,
            "n_examples": self.n_examples,
            "partition_sizes": dict(self.partition_sizes),
            "raw_oos": metric(self.raw_oos),
            "calibrated_oos": metric(self.calibrated_oos),
            "coverage": [asdict(c) for c in self.coverage],
            "reliability_diagram": (
                [asdict(b) for b in cal.diagram] if cal else []
            ),
            "threshold_coverage": (
                [asdict(c) for c in cal.threshold_coverage] if cal else []
            ),
            "directions": [asdict(d) for d in cal.directions] if cal else [],
            "verdict": self.verdict,
            "note": self.note,
            "notes": list(self.notes),
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
        }

    def summary(self) -> str:
        lines = [
            f"T27 real-data calibration — corpus={self.corpus_dir}",
            f"  instants={self.n_instants} examples={self.n_examples} "
            f"partitions={self.partition_sizes} horizon={self.label_horizon} "
            f"method={self.method}",
        ]
        if self.n_valid_sets or self.n_invalid_sets:
            lines.append(
                f"  sets: valid={self.n_valid_sets} invalid={self.n_invalid_sets}"
                + (" (valid-only run)" if self.valid_only else " (all sets fit)")
            )
        if self.publication_label:
            lines.append(f"  LABEL: {self.publication_label}")
        if self.calibrated_oos is not None:
            m = self.calibrated_oos.calibration
            lines.append(
                f"  OOS calibrated: brier={m.brier:.4f} "
                f"(skill={m.brier_skill:.4f}) ece={m.ece:.4f} mce={m.mce:.4f} "
                f"n={self.calibrated_oos.n}"
            )
            if self.raw_oos is not None:
                lines.append(
                    f"  OOS raw:        brier={self.raw_oos.calibration.brier:.4f} "
                    f"ece={self.raw_oos.calibration.ece:.4f}"
                )
            for tier in self.coverage:
                lines.append(
                    f"    tier {tier.tier:<6} coverage={tier.coverage:.3f} "
                    f"n={tier.count} accuracy={tier.accuracy:.3f} "
                    f"mean_p={tier.mean_probability:.3f} gap={tier.gap:+.3f}"
                )
            lines.append("  coverage at p>=threshold (RULE D confident subset):")
            for tc in self.threshold_coverage:
                lines.append(
                    f"    {tc.tier:<8} coverage={tc.coverage:.4f} n={tc.count} "
                    f"accuracy={tc.accuracy:.4f} mean_p={tc.mean_probability:.4f} "
                    f"gap={tc.gap:+.4f}"
                )
            lines.append("  directional (LONG p>=0.5 / SHORT p<0.5):")
            for d in self.directions:
                lines.append(
                    f"    {d.direction:<5} coverage={d.coverage:.4f} n={d.count} "
                    f"accuracy={d.accuracy:.4f} mean_p={d.mean_probability:.4f} "
                    f"brier={d.brier:.4f}"
                )
            lines.append("  reliability (predicted vs observed):")
            for b in self.reliability_diagram:
                lines.append(
                    f"    bin {b.index:>2} [{b.lower:.1f},{b.upper:.1f}) n={b.count} "
                    f"pred={b.mean_predicted:.4f} obs={b.empirical_rate:.4f} "
                    f"gap={b.gap:+.4f}"
                )
        else:
            lines.append("  OOS: not available (partition empty)")
        lines.append(f"  VERDICT: {self.verdict} — {self.note}")
        if self.walk_forward is not None:
            lines.append(self.walk_forward.summary())
        for extra in self.notes:
            lines.append(f"  note: {extra}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def _to_examples(
    feature_sets: Sequence[FeatureSet], closes: Sequence[float], label_horizon: int
) -> List[LabeledExample]:
    return build_labeled_examples(feature_sets, closes, horizon=label_horizon)


def _maybe_walk_forward(
    feature_sets, closes, label_horizon, method, model_factory,
    train, test, step, notes,
) -> Optional[WalkForwardSummary]:
    """Run the walk-forward when configured; degrade honestly if the corpus is short.

    A corpus that is too short for even one fold is recorded as a note, not raised:
    the caller still gets the partition verdict. This keeps the runner usable on a
    partial corpus (e.g. T25 landing year by year) without hiding the fact that the
    walk-forward could not run.
    """
    if train <= 0 or test <= 0:
        return None
    try:
        return walk_forward_calibration(
            feature_sets, closes, label_horizon=label_horizon, method=method,
            model_factory=model_factory, train_size=train, test_size=test, step=step,
        )
    except SplitError as exc:
        notes.append(f"walk-forward not run (corpus too short): {exc}")
        return None


def _logistic_factory(l2: float, max_iter: int = 60):
    """A model factory closing over the ridge strength for the pure-Python IRLS.

    `run_calibrated` and the walk-forward both accept `model_factory(x, y)`, so a
    single factory threads the same solver settings through every fit.
    """
    def factory(x, y):
        return fit_logistic(x, y, l2=l2, max_iter=max_iter)

    return factory


def _partition(
    examples: Sequence[LabeledExample],
    development_years: Sequence[int],
    validation_years: Sequence[int],
    oos_years: Sequence[int],
    mode: str = "auto",
    dev_fraction: float = 0.6,
    val_fraction: float = 0.2,
    notes: Optional[List[str]] = None,
):
    """Split examples chronologically.

    `mode`:
      * "year"     — calendar-year partitions (the Phase 2.0 default);
      * "fraction" — split the observed span by fraction (single-window corpora);
      * "auto"     — try "year"; if the corpus years are not covered (e.g. a
        rolling 2026 broker export), fall back to "fraction" and say so.

    Both paths are causal (dev < val < oos) and disjoint. The fallback is recorded
    in `notes`, never silent.
    """
    samples = [
        Sample(datetime.fromtimestamp(ex.timestamp, tz=timezone.utc), ex)
        for ex in examples
    ]
    if mode not in ("auto", "year", "fraction"):
        raise SplitError(f"unknown partition mode: {mode!r}")

    if mode in ("auto", "year"):
        try:
            split = chronological_split(
                samples, development_years, validation_years, oos_years
            )
            assert_causal(split)  # leak guard: dev < val < oos in time
            return split
        except SplitError as exc:
            if mode == "year":
                raise
            if notes is not None:
                notes.append(
                    "year partition not applicable ("
                    f"{exc}); using fraction partition "
                    f"dev={dev_fraction} val={val_fraction} of the observed span."
                )

    split = fractional_split(samples, dev_fraction, val_fraction)
    return split


def run_real_calibration(
    corpus_dir: str,
    *,
    close_key: str = DEFAULT_CLOSE_KEY,
    label_horizon: int = 1,
    method: str = "platt",
    model_factory: Optional[Callable] = None,
    development_years: Sequence[int] = DEVELOPMENT_YEARS,
    validation_years: Sequence[int] = VALIDATION_YEARS,
    oos_years: Sequence[int] = OOS_YEARS,
    walk_forward_train: int = 0,
    walk_forward_test: int = 0,
    walk_forward_step: int = 0,
    partition_mode: str = "auto",
    development_fraction: float = 0.6,
    validation_fraction: float = 0.2,
    l2: float = 1e-6,
    valid_filter: bool = False,
) -> RealCalibrationReport:
    """Run the T05 calibrated pipeline on a real corpus and apply the RULE C gate.

    Never tunes on OOS and never interpolates missing data. If a partition is
    empty the report says so and the verdict is `cannot_publish` — the absence is
    surfaced, not hidden.

    When `walk_forward_train` and `walk_forward_test` are both positive, a
    rolling-origin walk-forward is also run over the whole corpus; its pooled ECE
    is the more robust RULE C signal and is reported alongside the year split.

    `valid_filter` (F-T27-1): drop `valid=false` INCOMPLETE warm-up sets before
    fitting. Off by default to preserve every prior published number; the report
    always records the valid/invalid counts so an INCOMPLETE-trained headline is
    disclosed rather than hidden.
    """
    feature_sets, closes = load_corpus(corpus_dir, close_key=close_key)
    n_valid = sum(1 for fs in feature_sets if fs.valid)
    n_invalid = len(feature_sets) - n_valid
    if valid_filter:
        kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
        feature_sets = [fs for fs, _ in kept]
        closes = [c for _, c in kept]
    examples = _to_examples(feature_sets, closes, label_horizon)
    notes: List[str] = []
    if n_invalid:
        notes.append(
            f"{n_invalid} of {n_valid + n_invalid} feature sets are INCOMPLETE "
            "warm-up rows"
            + (
                " (dropped by valid_filter)."
                if valid_filter
                else " and were included in this run (F-T27-1); a headline trained "
                "on them is not evidential — re-run with valid_filter=True."
            )
        )
    if model_factory is None:
        model_factory = _logistic_factory(l2)
    split = _partition(
        examples, development_years, validation_years, oos_years,
        mode=partition_mode, dev_fraction=development_fraction,
        val_fraction=validation_fraction, notes=notes,
    )
    # A single observed window split by fraction is NOT out-of-sample evidence:
    # there is no held-out calendar period. Label it POC and never call it OOS.
    poc_mode = partition_mode == "fraction" or (
        partition_mode == "auto"
        and any("fraction partition" in n for n in notes)
    )
    publication_label = (
        "PROOF-OF-CONCEPT — single window (NOT out-of-sample)" if poc_mode else ""
    )
    if poc_mode:
        notes.append(
            "single-window fraction split: dev/val/oos come from ONE observed "
            "window; there is no held-out period. Label PROOF-OF-CONCEPT; do not "
            "claim walk-forward or out-of-sample."
        )

    development = [cast(LabeledExample, s.payload) for s in split.development]
    validation = [cast(LabeledExample, s.payload) for s in split.validation]
    oos = [cast(LabeledExample, s.payload) for s in split.oos]
    sizes = {
        "development": len(development),
        "validation": len(validation),
        "oos": len(oos),
    }

    wf = _maybe_walk_forward(
        feature_sets, closes, label_horizon, method, model_factory,
        walk_forward_train, walk_forward_test, walk_forward_step, notes,
    )

    if not development or not validation:
        note = (
            "cannot fit/calibrate: development and/or validation partition is "
            "empty; the verdict is NOT a probability."
        )
        return RealCalibrationReport(
            corpus_dir=corpus_dir,
            close_key=close_key,
            label_horizon=label_horizon,
            method=method,
            n_instants=len(feature_sets),
            n_examples=len(examples),
            partition_sizes=sizes,
            raw_oos=None,
            calibrated_oos=None,
            coverage=(),
            verdict=CANNOT_PUBLISH,
            note=note,
            notes=tuple(notes),
            walk_forward=wf,
            publication_label=publication_label,
            n_valid_sets=n_valid,
            n_invalid_sets=n_invalid,
            valid_only=valid_filter,
        )

    report: CalibratedReport = run_calibrated(
        development, validation, (oos or None), method=method, model_factory=model_factory
    )

    if not oos:
        notes.append(
            "OOS partition is empty — no out-of-sample evidence; cannot publish."
        )
        return RealCalibrationReport(
            corpus_dir=corpus_dir,
            close_key=close_key,
            label_horizon=label_horizon,
            method=method,
            n_instants=len(feature_sets),
            n_examples=len(examples),
            partition_sizes=sizes,
            raw_oos=None,
            calibrated_oos=None,
            coverage=(),
            verdict=CANNOT_PUBLISH,
            note="no OOS partition: out-of-sample evidence is required to publish.",
            notes=tuple(notes),
            walk_forward=wf,
            publication_label=publication_label,
            n_valid_sets=n_valid,
            n_invalid_sets=n_invalid,
            valid_only=valid_filter,
        )

    raw_oos = report.raw.get("oos")
    calibrated_oos = report.calibrated.get("oos")
    if calibrated_oos is None:
        raise SplitError("calibrated report is missing the OOS partition")

    ece = calibrated_oos.calibration.ece
    verdict, note = rule_c_verdict(ece)

    # RULE D — per-tier coverage on the calibrated OOS probabilities. The
    # calibrated probabilities are recoverable from the report's metrics only
    # through coverage_analysis, which the CalibrationReport already carries.
    coverage = tuple(calibrated_oos.calibration.coverage)
    empty_tiers = [t.tier for t in coverage if t.count == 0]
    if empty_tiers:
        notes.append(
            f"empty tiers (zero coverage, reported not hidden): {empty_tiers}"
        )
    if calibrated_oos.calibration.mce > calibrated_oos.calibration.ece:
        notes.append(
            f"MCE {calibrated_oos.calibration.mce:.4f} exceeds ECE "
            f"{calibrated_oos.calibration.ece:.4f} (a tier is worse than average)."
        )

    return RealCalibrationReport(
        corpus_dir=corpus_dir,
        close_key=close_key,
        label_horizon=label_horizon,
        method=method,
        n_instants=len(feature_sets),
        n_examples=len(examples),
        partition_sizes=sizes,
        raw_oos=raw_oos,
        calibrated_oos=calibrated_oos,
        coverage=coverage,
        verdict=verdict,
        note=note,
        notes=tuple(notes),
        walk_forward=wf,
        publication_label=publication_label,
        n_valid_sets=n_valid,
        n_invalid_sets=n_invalid,
        valid_only=valid_filter,
    )


# --------------------------------------------------------------------------
# CLI (path configurable — T28)
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="T27 real-data calibration runner")
    parser.add_argument(
        "--corpus",
        default="",
        help="directory of FeatureSet JSON "
        "(default: AURA_FEATURES_DIR / ASTRA_FEATURE_CORPUS / "
        "<repo>/research/features_real)",
    )
    parser.add_argument(
        "--close-key",
        default=os.environ.get(ENV_CLOSE_KEY, DEFAULT_CLOSE_KEY),
        help="sibling key holding the decision-bar close",
    )
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--method", default="platt", choices=["platt", "isotonic", "histogram"])
    parser.add_argument(
        "--partition-mode", default="auto", choices=["auto", "year", "fraction"],
        help="year (fall back=auto) or fraction split of a single time window",
    )
    parser.add_argument("--dev-fraction", type=float, default=0.6)
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument(
        "--l2", type=float, default=1e-6,
        help="ridge for the IRLS base fit (larger converges faster at p>>n)",
    )
    parser.add_argument("--wf-train", type=int, default=0, help="walk-forward train size")
    parser.add_argument("--wf-test", type=int, default=0, help="walk-forward test size")
    parser.add_argument("--wf-step", type=int, default=0, help="walk-forward step (0=test size)")
    parser.add_argument(
        "--valid-only", action="store_true",
        help="drop valid=false INCOMPLETE warm-up sets before fitting (F-T27-1)",
    )
    parser.add_argument("--out", default="", help="write the JSON report here")
    args = parser.parse_args(argv)

    corpus = resolve_corpus_dir(args.corpus)
    if not os.path.exists(corpus):
        print(
            f"no corpus at {corpus}: pass --corpus DIR|FILE or set AURA_FEATURES_DIR / "
            "ASTRA_FEATURE_CORPUS (T25 data has not landed).",
            file=sys.stderr,
        )
        return 2

    report = run_real_calibration(
        corpus,
        close_key=args.close_key,
        label_horizon=args.horizon,
        method=args.method,
        walk_forward_train=args.wf_train,
        walk_forward_test=args.wf_test,
        walk_forward_step=args.wf_step,
        partition_mode=args.partition_mode,
        development_fraction=args.dev_fraction,
        validation_fraction=args.val_fraction,
        l2=args.l2,
        valid_filter=args.valid_only,
    )
    print(report.summary())
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, sort_keys=True)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
