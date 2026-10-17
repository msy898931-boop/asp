"""Deterministic T15 validation demo — horizon calibration + SL/TP cost study.

SYNTHETIC data only (no XAUUSD in the container). This validates the T15 harness
and answers Q-horizon / Q-theta on synthetic data; it is NOT a market result and
publishes nothing (RULE C).

Run:
    python3 -m src.models.demo_levels
"""

from __future__ import annotations

from src.models.demo_baseline import SYNTHETIC_BANNER, _synthetic_series
from src.models.horizon import compare_horizons
from src.models.levels import (
    COST_TIERS,
    atr,
    compare_costs,
    suggest_levels,
    suggest_risk_percent,
)


def main(n: int = 3000) -> int:
    features, closes = _synthetic_series(n)

    print(SYNTHETIC_BANNER)
    print(f"series: {n} bars over 2021-2025 (synthetic)")
    print()

    # --- Q-horizon: calibrate each candidate horizon under the conservative
    # cost dead-band. horizon 1 = next bar, 4 ~ 1h on M15, 16 ~ 4h. ---
    print("Q-horizon — OOS calibration by horizon (conservative dead-band):")
    results = compare_horizons(features, closes, horizons=(1, 4, 16))
    for r in results:
        dist = r.distribution
        print(
            f"  H={r.horizon:<2} theta={r.theta:.2f} "
            f"UP={dist['UP']['share']:.2f} DOWN={dist['DOWN']['share']:.2f} "
            f"FLAT={dist['FLAT']['share']:.2f} | "
            f"n={r.n} brier={r.brier:.4f} ece={r.ece:.4f} "
            f"skill={r.brier_skill:+.3f} -> {r.label.upper()}"
        )
    print(
        "  => no horizon recommendable on synthetic data (H=1 is a generator "
        "artifact; the ranking is not informative)."
    )
    print()

    # --- Q-theta / RULE B: SL/TP and cost-tier expectancy on a sample of setups. ---
    print("RULE B — SL/TP expectancy under three cost tiers (sample of setups):")
    print("  (atr_1.5x SL, rr_2x TP; gross expectancy in R before cost)")

    sample_every = 137
    agg = {t.name: [0.0, 0.0, 0] for t in COST_TIERS}  # sum net_r, sum gross, count
    atr_value = atr(closes, period=14)
    for i in range(30, n - 40, sample_every):
        direction = "UP" if closes[i + 4] > closes[i] else "DOWN"
        levels = suggest_levels(closes[i], direction, atr_value, atr_mult=1.5, rr=2.0)
        for outcome in compare_costs(closes, i, direction, levels, max_bars=16):
            bucket = agg[outcome.tier]
            bucket[0] += outcome.net_expectancy_r
            bucket[1] += outcome.gross_expectancy_r
            bucket[2] += 1
    for tier in COST_TIERS:
        net, gross, count = agg[tier.name]
        if count == 0:
            continue
        flag = "decision-grade" if tier.decision_grade else "reference only"
        print(
            f"  {tier.name:<13} round_trip={tier.round_trip:.2f} "
            f"mean_gross={gross / count:+.3f}R mean_net={net / count:+.3f}R "
            f"[{flag}]"
        )
    print()

    # --- Risk suggestion is a tier mapping, not a position size. ---
    print("Risk suggestion by probability tier (suggestion only, no order):")
    for p in (0.2, 0.5, 0.8):
        print(f"  p={p:.1f} -> suggested risk {suggest_risk_percent(p):.2f}%")
    print()
    print(
        "NOTE: synthetic only. H1/H4-style horizons are approximated by bar counts "
        "here; theta is the conservative round-trip cost. Unpublished (RULE C)."
    )
    print(
        "CAVEAT: the near-perfect H=1 calibration is an artifact of the synthetic "
        "generator (the latent state that drives price also drives the features), "
        "not model skill. It is a red flag for the generator, not evidence."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
