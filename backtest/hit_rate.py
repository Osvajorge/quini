"""Compute top-1 hit rates for the model.

For each holdout match:
  - 1X2 top-1 = side with highest model prob; hit if it matches result
  - Exact score top-1 = (h, a) cell with highest joint prob; hit if matches
  - Exact score top-3 = result in top 3 most likely scorelines

Uses the same walk-forward refit cadence as the accuracy backtest, so the
hit rate is out-of-sample for every match.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from model.data_loader import load_matches
from model.dixon_coles import DCFit, fit
from model.markets import all_markets, score_matrix


def run(holdout_start: str = "2024-01-01", holdout_end: str = "2026-06-17", cadence_days: int = 60) -> dict:
    DCFit.__module__ = "model.dixon_coles"
    all_df = load_matches()
    holdout = all_df[(all_df["date"] >= holdout_start) & (all_df["date"] <= holdout_end)].copy()
    holdout = holdout.sort_values("date").reset_index(drop=True)
    print(f"holdout: {len(holdout)} matches")

    start = holdout["date"].min().normalize()
    end = holdout["date"].max().normalize()
    refit_dates = []
    cur = start
    while cur <= end:
        refit_dates.append(cur)
        cur = cur + pd.Timedelta(days=cadence_days)

    # Counters
    n = 0
    hit_1x2 = 0
    hit_score_top1 = 0
    hit_score_top3 = 0
    hit_btts = 0
    hit_over25 = 0
    confusion_1x2 = {"home_home": 0, "home_draw": 0, "home_away": 0,
                     "draw_home": 0, "draw_draw": 0, "draw_away": 0,
                     "away_home": 0, "away_draw": 0, "away_away": 0}
    n_skipped = 0

    for i, refit_date in enumerate(refit_dates):
        window_end = (
            refit_dates[i + 1] if i + 1 < len(refit_dates)
            else holdout["date"].max() + pd.Timedelta(days=1)
        )
        as_of_train = load_matches(as_of=refit_date)
        as_of_train = as_of_train[as_of_train["date"] < refit_date]
        if len(as_of_train) < 500:
            continue

        print(f"  [{i+1}/{len(refit_dates)}] refit {refit_date.date()} · train={len(as_of_train)}")
        f = fit(as_of_train, verbose=False)

        window = holdout[(holdout["date"] >= refit_date) & (holdout["date"] < window_end)]
        for _, m in window.iterrows():
            h_team, a_team = m["home_team"], m["away_team"]
            if h_team not in f.alpha or a_team not in f.alpha:
                n_skipped += 1
                continue
            lh, la = f.expected_goals(h_team, a_team, neutral=bool(m["neutral"]))
            mat = score_matrix(lh, la, f.rho, max_goals=10)

            # 1X2 top-1
            p = all_markets(mat)["1x2"]
            sides = ["home", "draw", "away"]
            probs = [p["home"], p["draw"], p["away"]]
            top_1x2 = sides[int(np.argmax(probs))]

            hs, as_ = int(m["home_score"]), int(m["away_score"])
            diff = hs - as_
            actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")

            if top_1x2 == actual:
                hit_1x2 += 1
            confusion_1x2[f"{top_1x2}_{actual}"] += 1

            # Exact score top-1 and top-3
            flat = [(h, a, mat[h, a]) for h in range(mat.shape[0]) for a in range(mat.shape[1])]
            flat.sort(key=lambda t: t[2], reverse=True)
            top1 = flat[0]
            top3 = set((t[0], t[1]) for t in flat[:3])
            if top1[0] == hs and top1[1] == as_:
                hit_score_top1 += 1
            if (hs, as_) in top3:
                hit_score_top3 += 1

            # BTTS and Over 2.5 (top-1)
            markets = all_markets(mat)
            btts_pred = "yes" if markets["btts"]["yes"] > 0.5 else "no"
            btts_actual = "yes" if (hs > 0 and as_ > 0) else "no"
            if btts_pred == btts_actual:
                hit_btts += 1

            over_pred = "over" if markets["ou_2_5"]["over"] > 0.5 else "under"
            over_actual = "over" if (hs + as_) > 2 else "under"
            if over_pred == over_actual:
                hit_over25 += 1

            n += 1

    print()
    print("=" * 70)
    print(f"matches evaluated: {n} (skipped {n_skipped})")
    print("=" * 70)
    print(f"1X2 top-1 hit rate         : {hit_1x2/n*100:.1f}%  ({hit_1x2}/{n})")
    print(f"Exact score top-1 hit rate : {hit_score_top1/n*100:.1f}%  ({hit_score_top1}/{n})")
    print(f"Exact score top-3 hit rate : {hit_score_top3/n*100:.1f}%  ({hit_score_top3}/{n})")
    print(f"BTTS top-1 hit rate        : {hit_btts/n*100:.1f}%  ({hit_btts}/{n})")
    print(f"Over/Under 2.5 hit rate    : {hit_over25/n*100:.1f}%  ({hit_over25}/{n})")
    print()
    print("Baselines for comparison:")
    print("  Random 1X2:           33.3%")
    print("  Always pick home:     ~46% (home-bias in football)")
    print("  Random exact score:   ~3% (top of distribution)")
    print("  Random BTTS:          50%")
    print()
    print("1X2 confusion matrix (predicted ↓ vs actual →):")
    print(f"  {'':6} {'home':>7} {'draw':>7} {'away':>7}")
    for pred in ["home", "draw", "away"]:
        print(f"  {pred:6} {confusion_1x2[f'{pred}_home']:>7} {confusion_1x2[f'{pred}_draw']:>7} {confusion_1x2[f'{pred}_away']:>7}")

    return {
        "n": n,
        "hit_1x2": hit_1x2 / n,
        "hit_score_top1": hit_score_top1 / n,
        "hit_score_top3": hit_score_top3 / n,
        "hit_btts": hit_btts / n,
        "hit_over25": hit_over25 / n,
        "confusion_1x2": confusion_1x2,
    }


if __name__ == "__main__":
    run()
