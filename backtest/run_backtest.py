"""Accuracy-only backtest. Rolling refit by cadence, Brier + log-loss per market.

Walk-forward:
  for each refit_date in cadence:
    fit on all matches before refit_date
    predict every holdout match in [refit_date, refit_date + cadence)
    log market predictions vs observed outcome
  aggregate Brier + log-loss per market and compare to baselines

This is the cheap version of the gate — it tells us if the model has signal
in probability space. ROI gate requires historical closing odds (separate run).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from model.data_loader import load_matches
from model.dixon_coles import DCFit, fit
from model.markets import all_markets, score_matrix

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
LOG_LOSS_EPS = 1e-9


@dataclass
class MarketResult:
    name: str
    n: int = 0
    brier_sum: float = 0.0
    logloss_sum: float = 0.0
    base_brier_sum: float = 0.0
    base_logloss_sum: float = 0.0

    def add(self, p_model: float, p_base: float, actual: int) -> None:
        self.n += 1
        self.brier_sum += (p_model - actual) ** 2
        self.logloss_sum += -np.log(np.clip(p_model if actual else 1 - p_model, LOG_LOSS_EPS, 1.0))
        self.base_brier_sum += (p_base - actual) ** 2
        self.base_logloss_sum += -np.log(np.clip(p_base if actual else 1 - p_base, LOG_LOSS_EPS, 1.0))

    def summary(self) -> dict:
        if self.n == 0:
            return {"n": 0}
        return {
            "n": self.n,
            "brier_model": self.brier_sum / self.n,
            "brier_baseline": self.base_brier_sum / self.n,
            "brier_skill": 1.0 - (self.brier_sum / self.base_brier_sum) if self.base_brier_sum > 0 else 0.0,
            "logloss_model": self.logloss_sum / self.n,
            "logloss_baseline": self.base_logloss_sum / self.n,
        }


@dataclass
class MultiClassResult:
    name: str
    classes: list[str]
    n: int = 0
    brier_sum: float = 0.0
    logloss_sum: float = 0.0
    base_brier_sum: float = 0.0
    base_logloss_sum: float = 0.0

    def add(self, p_model: np.ndarray, p_base: np.ndarray, actual_idx: int) -> None:
        self.n += 1
        # Multi-class Brier = sum over classes of (p_k - 1_{k=actual})^2
        actual_vec = np.zeros(len(self.classes))
        actual_vec[actual_idx] = 1.0
        self.brier_sum += float(np.sum((p_model - actual_vec) ** 2))
        self.base_brier_sum += float(np.sum((p_base - actual_vec) ** 2))
        self.logloss_sum += float(-np.log(np.clip(p_model[actual_idx], LOG_LOSS_EPS, 1.0)))
        self.base_logloss_sum += float(-np.log(np.clip(p_base[actual_idx], LOG_LOSS_EPS, 1.0)))

    def summary(self) -> dict:
        if self.n == 0:
            return {"n": 0}
        return {
            "n": self.n,
            "brier_model": self.brier_sum / self.n,
            "brier_baseline": self.base_brier_sum / self.n,
            "brier_skill": 1.0 - (self.brier_sum / self.base_brier_sum) if self.base_brier_sum > 0 else 0.0,
            "logloss_model": self.logloss_sum / self.n,
            "logloss_baseline": self.base_logloss_sum / self.n,
        }


def _frequency_baseline(train: pd.DataFrame) -> dict:
    """Compute marginal frequencies on training set for each market."""
    h = train["home_score"].to_numpy()
    a = train["away_score"].to_numpy()
    diff = h - a
    p_1x2 = np.array([(diff > 0).mean(), (diff == 0).mean(), (diff < 0).mean()])
    p_over25 = float(((h + a) > 2.5).mean())
    p_btts = float(((h > 0) & (a > 0)).mean())
    p_home_ah15 = float((diff >= 2).mean())
    return {
        "1x2": p_1x2,
        "over_2_5": p_over25,
        "btts": p_btts,
        "ah_home_-1_5": p_home_ah15,
    }


def _refit_dates(holdout: pd.DataFrame, cadence_days: int) -> list[pd.Timestamp]:
    start = holdout["date"].min().normalize()
    end = holdout["date"].max().normalize()
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur)
        cur = cur + pd.Timedelta(days=cadence_days)
    return dates


def run(
    holdout_start: str = "2024-01-01",
    holdout_end: str | None = None,
    cadence_days: int = 60,
    min_train_date: str = "2005-01-01",
    output_json: Path | None = None,
) -> dict:
    all_df = load_matches(min_date=min_train_date)
    all_df = all_df.sort_values("date").reset_index(drop=True)

    holdout = all_df[all_df["date"] >= pd.Timestamp(holdout_start)].copy()
    if holdout_end:
        holdout = holdout[holdout["date"] <= pd.Timestamp(holdout_end)].copy()
    holdout = holdout.reset_index(drop=True)

    print(f"holdout: {len(holdout)} matches  {holdout.date.min().date()} → {holdout.date.max().date()}")
    print(f"cadence: refit every {cadence_days} days")

    refit_dates = _refit_dates(holdout, cadence_days)
    print(f"refits scheduled: {len(refit_dates)}")

    results = {
        "1x2": MultiClassResult("1X2", ["home", "draw", "away"]),
        "over_2_5": MarketResult("Over 2.5"),
        "btts": MarketResult("BTTS Yes"),
        "ah_home_-1_5": MarketResult("AH Home -1.5"),
    }

    n_predicted = 0
    n_skipped_unknown_team = 0

    for i, refit_date in enumerate(refit_dates):
        window_end = (
            refit_dates[i + 1] if i + 1 < len(refit_dates)
            else holdout["date"].max() + pd.Timedelta(days=1)
        )
        train = all_df[all_df["date"] < refit_date].copy()
        # Re-weight train relative to refit_date so time decay is honest.
        as_of_train = load_matches(min_date=min_train_date, as_of=refit_date)
        as_of_train = as_of_train[as_of_train["date"] < refit_date]
        if len(as_of_train) < 500:
            print(f"  skip refit {refit_date.date()}: only {len(as_of_train)} train rows")
            continue

        print(f"\n[{i+1}/{len(refit_dates)}] refit {refit_date.date()} · train={len(as_of_train)} rows")
        DCFit.__module__ = "model.dixon_coles"
        f = fit(as_of_train, verbose=False)
        baseline = _frequency_baseline(as_of_train)

        window = holdout[(holdout["date"] >= refit_date) & (holdout["date"] < window_end)]
        print(f"  predicting {len(window)} matches in window")

        for _, m in window.iterrows():
            h_team, a_team = m["home_team"], m["away_team"]
            if h_team not in f.alpha or a_team not in f.alpha:
                n_skipped_unknown_team += 1
                continue
            lh, la = f.expected_goals(h_team, a_team, neutral=bool(m["neutral"]))
            mat = score_matrix(lh, la, f.rho)
            probs = all_markets(mat)

            hs, as_ = int(m["home_score"]), int(m["away_score"])
            diff = hs - as_

            # 1X2
            actual_1x2 = 0 if diff > 0 else (1 if diff == 0 else 2)
            p_model_1x2 = np.array([probs["1x2"]["home"], probs["1x2"]["draw"], probs["1x2"]["away"]])
            results["1x2"].add(p_model_1x2, baseline["1x2"], actual_1x2)

            # Over 2.5
            results["over_2_5"].add(
                probs["ou_2_5"]["over"], baseline["over_2_5"], int((hs + as_) > 2)
            )
            # BTTS
            results["btts"].add(
                probs["btts"]["yes"], baseline["btts"], int(hs > 0 and as_ > 0)
            )
            # AH home -1.5
            results["ah_home_-1_5"].add(
                probs["ah_home_-1_5"]["home_cover"], baseline["ah_home_-1_5"], int(diff >= 2)
            )

            n_predicted += 1

    print(f"\npredictions made: {n_predicted}")
    print(f"skipped (team not in train): {n_skipped_unknown_team}")

    summary = {
        "config": {
            "holdout_start": holdout_start,
            "holdout_end": holdout_end,
            "cadence_days": cadence_days,
            "min_train_date": min_train_date,
        },
        "n_predicted": n_predicted,
        "n_skipped": n_skipped_unknown_team,
        "markets": {name: r.summary() for name, r in results.items()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    print("\n" + "=" * 78)
    print(f"{'Market':18} {'N':>5} {'Brier(mod)':>12} {'Brier(base)':>12} {'Skill':>8} {'LL(mod)':>10} {'LL(base)':>10}")
    print("-" * 78)
    for name, r in results.items():
        s = r.summary()
        if s.get("n", 0) == 0:
            continue
        print(
            f"{name:18} {s['n']:>5} "
            f"{s['brier_model']:>12.4f} "
            f"{s['brier_baseline']:>12.4f} "
            f"{s['brier_skill']:>+8.2%} "
            f"{s['logloss_model']:>10.4f} "
            f"{s['logloss_baseline']:>10.4f}"
        )
    print("=" * 78)
    print()
    print("INTERPRETATION:")
    print("  brier_skill > 0  → model beats marginal-frequency baseline")
    print("  brier_skill > 5% → meaningful signal worth backtesting against odds")
    print("  brier_skill < 0  → model is worse than guessing the long-run frequency")

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        # Make summary serialisable
        def _ser(o):
            if isinstance(o, (np.floating, np.integer)):
                return float(o)
            raise TypeError
        with open(output_json, "w") as fp:
            json.dump(summary, fp, indent=2, default=_ser)
        print(f"\nreport saved → {output_json}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-start", default="2024-01-01")
    parser.add_argument("--holdout-end", default=None)
    parser.add_argument("--cadence-days", type=int, default=60)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_path = args.out or REPORTS_DIR / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    run(
        holdout_start=args.holdout_start,
        holdout_end=args.holdout_end,
        cadence_days=args.cadence_days,
        output_json=out_path,
    )
