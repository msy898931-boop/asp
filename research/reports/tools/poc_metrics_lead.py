"""Lead independent POC metric pass — MT5 3.5-month corpus, single-window split.
Not the verdict artifact (Agent-B owns T27); this is an independent cross-check of
the exact human-requested metrics: Brier, ECE, reliability, tier coverage at
0.55/0.60/0.65, directional accuracy, LONG vs SHORT. No walk-forward is claimed."""
import json

from src.models.calibration import (
    brier_score,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_diagram,
)
from src.models.calibrators import fit_calibrator
from src.models.dataset import build_labeled_examples, feature_columns, to_matrix
from src.models.logistic import fit_logistic
from src.models.realdata import load_corpus
from src.models.splits import fractional_split

CORPUS = "research/features_real/corpus/real_corpus.json.gz"
fs, closes = load_corpus(CORPUS)
examples = build_labeled_examples(fs, closes, horizon=1)
# fractional single-window split (same as --partition-mode fraction)
class S:  # minimal sample view for fractional_split
    pass
samples = [type("X", (), {"timestamp": __import__("datetime").datetime.fromtimestamp(
    e.timestamp, __import__("datetime").timezone.utc), "payload": e})() for e in examples]
split = fractional_split(samples, 0.6, 0.2)
dev = [s.payload for s in split.development]
val = [s.payload for s in split.validation]
oos = [s.payload for s in split.oos]
cols = feature_columns(dev)
xd, yd = to_matrix(dev, cols)
model = fit_logistic(xd, yd, l2=0.01)
xv, yv = to_matrix(val, cols)
cal = fit_calibrator("platt", [model.decision_function(r) for r in xv], yv)
xo, yo = to_matrix(oos, cols)
probs = cal.transform_batch([model.decision_function(r) for r in xo])

brier = brier_score(probs, yo)
ece = expected_calibration_error(probs, yo)
mce = maximum_calibration_error(probs, yo)
base = sum(yo) / len(yo)
skill = 1 - brier / (base * (1 - base))
diagram = reliability_diagram(probs, yo)
pred_up = [1 if p >= 0.5 else 0 for p in probs]
dir_acc = sum(1 for a, b in zip(pred_up, yo) if a == b) / len(yo)
longs = [(p, y) for p, y in zip(probs, yo) if p >= 0.5]
shorts = [(p, y) for p, y in zip(probs, yo) if p < 0.5]
def tier(thr):
    m = [(p, y) for p, y in zip(probs, yo) if p >= thr]
    if not m:
        return {"threshold": thr, "n": 0, "coverage": 0.0, "accuracy": 0.0, "mean_p": 0.0}
    return {"threshold": thr, "n": len(m), "coverage": len(m) / len(yo),
            "accuracy": sum(y for _, y in m) / len(m),
            "mean_p": sum(p for p, _ in m) / len(m)}
out = {
    "corpus": CORPUS, "window": "2026-06-24..2026-10-08",
    "n_instants": len(fs), "n_examples": len(examples),
    "split": {"dev": len(dev), "val": len(val), "oos": len(oos)},
    "base_rate_oos": base,
    "brier": brier, "ece": ece, "mce": mce, "brier_skill": skill,
    "directional_accuracy": dir_acc,
    "long": {"n": len(longs), "hit_rate": (sum(y for _, y in longs) / len(longs)) if longs else 0.0},
    "short": {"n": len(shorts), "hit_rate": (sum(1 - y for _, y in shorts) / len(shorts)) if shorts else 0.0},
    "tiers": [tier(0.55), tier(0.60), tier(0.65)],
    "reliability": [{"bin_lo": b.lower, "bin_hi": b.upper, "n": b.count,
                     "mean_p": b.mean_predicted, "empirical": b.empirical_rate,
                     "gap": b.gap} for b in diagram],
    "label": "PROOF-OF-CONCEPT — single window (no walk-forward claimed)",
}
print(json.dumps(out, indent=1))
with open("/tmp/poc_metrics_lead.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(out, indent=1))
