"""Paso 0 validation — honest gate before any model improvement or deploy.

Three checks:
  0a. Bootstrap BCa CI on ROI per market (fat-tail aware)
  0b. Temporal split — sign + magnitude consistency (not p-value)
  0d. Benjamini-Hochberg FDR control over the family of tested cells

p-values come from the bootstrap itself (fraction of resamples with ROI ≤ 0),
so they are coherent with the CI. Family for BH = every market in the bet log.

Run after a `backtest.roi` invocation:
    python -m backtest.validate path/to/roi_xxx.bets.csv
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
N_BOOT = 10000
SEED = 42


# ---------------------------------------------------------------------------
# 0a. Bootstrap BCa
# ---------------------------------------------------------------------------
def roi_of(profits: np.ndarray) -> float:
    return float(profits.mean()) if len(profits) else 0.0


def bootstrap_roi_bca(profits: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """BCa bootstrap of the ROI = mean(P/L per bet).

    BCa corrects for bias and skewness in the bootstrap distribution. Critical
    when P/L is asymmetric (lose 1u or win odds-1, with large odds).
    """
    rng = np.random.default_rng(seed)
    n = len(profits)
    if n < 5:
        return {"n": n, "note": "too small for BCa"}

    theta_hat = roi_of(profits)
    idx = rng.integers(0, n, size=(n_boot, n))
    boot = profits[idx].mean(axis=1)

    # z0: bias correction
    prop_less = np.mean(boot < theta_hat)
    prop_less = float(np.clip(prop_less, 1e-6, 1 - 1e-6))
    z0 = stats.norm.ppf(prop_less)

    # a: acceleration via jackknife
    jack = np.array([roi_of(np.delete(profits, i)) for i in range(n)])
    jack_mean = jack.mean()
    diff = jack_mean - jack
    denom = 6.0 * (np.sum(diff ** 2) ** 1.5)
    a = float(np.sum(diff ** 3) / denom) if denom != 0 else 0.0

    def bca_pct(alpha: float) -> float:
        z_alpha = stats.norm.ppf(alpha)
        adj = z0 + (z0 + z_alpha) / (1 - a * (z0 + z_alpha))
        return float(np.clip(stats.norm.cdf(adj) * 100, 0.0, 100.0))

    lo = float(np.percentile(boot, bca_pct(0.025)))
    hi = float(np.percentile(boot, bca_pct(0.975)))
    p5 = float(np.percentile(boot, bca_pct(0.05)))
    p_value = float(np.mean(boot <= 0))  # one-sided H0: ROI ≤ 0

    return {
        "n": n,
        "roi": theta_hat,
        "boot_mean": float(boot.mean()),
        "ci95_low": lo,
        "ci95_high": hi,
        "p5_bca": p5,
        "p_value": p_value,
        "z0": float(z0),
        "a": a,
    }


# ---------------------------------------------------------------------------
# 0b. Temporal split — sign + magnitude consistency
# ---------------------------------------------------------------------------
def temporal_split(df: pd.DataFrame) -> dict:
    if len(df) < 4:
        return {"n": len(df), "note": "too small for split"}
    df = df.sort_values("date").reset_index(drop=True)
    mid = len(df) // 2
    h1, h2 = df.iloc[:mid], df.iloc[mid:]

    def summ(d: pd.DataFrame) -> dict:
        p = d["pl"].to_numpy()
        return {
            "n": int(len(d)),
            "roi": roi_of(p),
            "date_from": str(d["date"].min())[:10],
            "date_to": str(d["date"].max())[:10],
            "wins": int(d["won"].sum()),
        }

    r1, r2 = summ(h1), summ(h2)
    both_positive = r1["roi"] > 0 and r2["roi"] > 0
    if both_positive:
        ratio = min(r1["roi"], r2["roi"]) / max(r1["roi"], r2["roi"])
    else:
        ratio = float("nan")
    # Heuristic: ratio > 0.4 means neither half is < 40% of the other.
    robust = bool(both_positive and ratio > 0.4)

    return {
        "half_1": r1,
        "half_2": r2,
        "both_positive": both_positive,
        "ratio_min_over_max": ratio,
        "robust_signal": robust,
    }


# ---------------------------------------------------------------------------
# 0d. Benjamini-Hochberg
# ---------------------------------------------------------------------------
def benjamini_hochberg(pvals: dict[str, float], alpha: float = 0.05) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    max_k = 0
    survivors: dict[str, dict] = {}
    for k, (name, p) in enumerate(items, start=1):
        thr = (k / m) * alpha
        if p <= thr:
            max_k = k
    for k, (name, p) in enumerate(items, start=1):
        thr = (k / m) * alpha
        survivors[name] = {
            "p_value": p,
            "rank": k,
            "bh_threshold": thr,
            "survives": k <= max_k,
        }
    return {"family_size": m, "alpha": alpha, "cells": survivors}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def validate(bet_log: Path) -> dict:
    df = pd.read_csv(bet_log, parse_dates=["date"])
    print(f"loaded {len(df)} bets · markets: {sorted(df.market.unique())}")
    print(f"bet log date range: {df.date.min().date()} → {df.date.max().date()}\n")

    per_market: dict[str, dict] = {}
    pvals: dict[str, float] = {}

    for market in sorted(df.market.unique()):
        sub = df[df.market == market]
        profits = sub.pl.to_numpy()
        boot = bootstrap_roi_bca(profits)
        split = temporal_split(sub)
        per_market[market] = {"bootstrap": boot, "temporal_split": split}
        if boot.get("n", 0) >= 5:
            pvals[market] = boot["p_value"]

    bh = benjamini_hochberg(pvals, alpha=0.05)

    # Pretty table
    print("=" * 130)
    print(
        f"{'Market':16} {'N':>4} {'ROI':>8} {'CI5%':>8} {'CI95%':>9} {'pBoot':>8} "
        f"{'1stH ROI':>9} {'2ndH ROI':>9} {'both+':>6} {'ratio':>6} {'robust':>7} {'BH ok':>6}"
    )
    print("-" * 130)
    for market in sorted(per_market.keys()):
        m = per_market[market]
        b = m["bootstrap"]
        s = m["temporal_split"]
        bh_cell = bh["cells"].get(market, {})
        if b.get("n", 0) < 5:
            print(f"{market:16} {b.get('n', 0):>4}  (skipped — too small)")
            continue
        h1 = s.get("half_1", {})
        h2 = s.get("half_2", {})
        ratio = s.get("ratio_min_over_max")
        ratio_str = f"{ratio:.2f}" if isinstance(ratio, float) and not np.isnan(ratio) else "  —"
        robust_str = "✓" if s.get("robust_signal") else "✗"
        bh_str = "✓" if bh_cell.get("survives") else "✗"
        print(
            f"{market:16} "
            f"{b['n']:>4} "
            f"{b['roi']*100:>+7.2f}% "
            f"{b['p5_bca']*100:>+7.2f}% "
            f"{b['ci95_high']*100:>+8.2f}% "
            f"{b['p_value']:>8.4f} "
            f"{h1.get('roi', 0)*100:>+8.2f}% "
            f"{h2.get('roi', 0)*100:>+8.2f}% "
            f"{'✓' if s.get('both_positive') else '✗':>6} "
            f"{ratio_str:>6} "
            f"{robust_str:>7} "
            f"{bh_str:>6}"
        )
    print("=" * 130)

    print("\nINTERPRETATION:")
    print("  CI5% > 0       → bootstrap-BCa says ROI > 0 at 95% confidence")
    print("  both+ + ratio  → first and second half are both positive and within 40% of each other")
    print("  BH ok          → cell survives Benjamini-Hochberg at FDR 0.05 across family of")
    print(f"                   {bh['family_size']} tested markets")
    print("\nDEPLOY GATE: a market is deployable iff CI5% > 0 AND robust AND BH ok.")

    # Write summary JSON
    out = {
        "bet_log": str(bet_log),
        "n_bets_total": int(len(df)),
        "family_size": bh["family_size"],
        "per_market": per_market,
        "bh_correction": bh,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out_path = bet_log.with_suffix(".validation.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nreport → {out_path}")

    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("bet_log", type=Path, nargs="?", help="Path to *.bets.csv")
    args = p.parse_args()

    if args.bet_log is None:
        candidates = sorted(REPORTS_DIR.glob("*.bets.csv"))
        if not candidates:
            print("No bet log found. Run `python -m backtest.roi` first.")
            raise SystemExit(1)
        args.bet_log = candidates[-1]
        print(f"using most recent: {args.bet_log.name}\n")

    validate(args.bet_log)
