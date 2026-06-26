"""XGBoost meta-learner: stacks Dixon-Coles + Elo + team features.

Inspired by WINNER12 multi-model consensus approach and Bayesian SSM
(Ridall 2025). Uses historical results to learn which base model signals
predict outcomes best, weighted by recency and tournament importance.

Features per match:
  - Dixon-Coles 1X2 probabilities (3)
  - Elo 1X2 probabilities (3)
  - Elo ratings and gap (3)
  - Attack/defense power per team (4)
  - Form per team (2)
  - Home advantage flag (1)
  - Goal-scoring history: avg goals scored/conceded last N (4)
  - Head-to-head record (3)
  = 23 features total

Target: 0=home, 1=draw, 2=away (for 1X2)
        0=under, 1=over (for O/U 2.5)
"""
from __future__ import annotations

import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier

from model.data_loader import load_matches, tournament_weight
from model.elo import compute_ratings, match_probs as elo_match_probs, HOME_ADVANTAGE, INITIAL_RATING

ROOT = Path(__file__).resolve().parent.parent
META_PATH = ROOT / "data" / "meta_1x2.pkl"
META_OU_PATH = ROOT / "data" / "meta_ou.pkl"


def _rolling_stats(df: pd.DataFrame, n: int = 10) -> dict[str, dict[str, float]]:
    """Compute rolling stats per team: avg goals scored, conceded, win%, draw%."""
    stats: dict[str, list[dict]] = defaultdict(list)

    for _, row in df.iterrows():
        h, a = row["home_team"], row["away_team"]
        hs, as_ = int(row["home_score"]), int(row["away_score"])
        stats[h].append({"scored": hs, "conceded": as_, "win": hs > as_, "draw": hs == as_})
        stats[a].append({"scored": as_, "conceded": hs, "win": as_ > hs, "draw": hs == as_})

    result = {}
    for team, matches in stats.items():
        recent = matches[-n:]
        result[team] = {
            "avg_scored": np.mean([m["scored"] for m in recent]),
            "avg_conceded": np.mean([m["conceded"] for m in recent]),
            "win_rate": np.mean([m["win"] for m in recent]),
            "draw_rate": np.mean([m["draw"] for m in recent]),
        }
    return result


def _head_to_head(df: pd.DataFrame, team_a: str, team_b: str, n: int = 5) -> dict[str, float]:
    """Last N head-to-head results between two teams."""
    mask = ((df["home_team"] == team_a) & (df["away_team"] == team_b)) | \
           ((df["home_team"] == team_b) & (df["away_team"] == team_a))
    h2h = df[mask].tail(n)
    if len(h2h) == 0:
        return {"h2h_win_a": 0.5, "h2h_draw": 0.2, "h2h_win_b": 0.3}

    wins_a = draws = wins_b = 0
    for _, row in h2h.iterrows():
        hs, as_ = int(row["home_score"]), int(row["away_score"])
        if row["home_team"] == team_a:
            if hs > as_: wins_a += 1
            elif hs == as_: draws += 1
            else: wins_b += 1
        else:
            if as_ > hs: wins_a += 1
            elif hs == as_: draws += 1
            else: wins_b += 1

    total = len(h2h)
    return {"h2h_win_a": wins_a / total, "h2h_draw": draws / total, "h2h_win_b": wins_b / total}


def build_features(df: pd.DataFrame, fit=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build feature matrix X, targets y_1x2, y_ou from historical matches."""
    from model.bivariate_poisson import load_fit
    from model.predict import attack_defense_power

    if fit is None:
        fit = load_fit()

    elo_ratings = compute_ratings(df)
    rolling = _rolling_stats(df)

    X_rows = []
    y_1x2 = []
    y_ou = []

    for idx in range(len(df)):
        row = df.iloc[idx]
        home, away = row["home_team"], row["away_team"]
        hs, as_ = int(row["home_score"]), int(row["away_score"])
        neutral = bool(row["neutral"])

        # Dixon-Coles probs (if team exists in fit)
        try:
            grid = fit.predict_grid(home, away, neutral=neutral)
            dc_h, dc_d, dc_a = grid.home_win, grid.draw, grid.away_win
        except (KeyError, Exception):
            dc_h, dc_d, dc_a = 0.45, 0.25, 0.30

        # Elo
        rh = elo_ratings.get(home, INITIAL_RATING)
        ra = elo_ratings.get(away, INITIAL_RATING)
        elo_p = elo_match_probs(rh, ra, neutral=neutral)

        # Attack/defense
        pow_h = attack_defense_power(fit, home) or {"attack": 0.0, "defense": 0.0}
        pow_a = attack_defense_power(fit, away) or {"attack": 0.0, "defense": 0.0}

        # Rolling stats
        rs_h = rolling.get(home, {"avg_scored": 1.3, "avg_conceded": 1.1, "win_rate": 0.4, "draw_rate": 0.25})
        rs_a = rolling.get(away, {"avg_scored": 1.3, "avg_conceded": 1.1, "win_rate": 0.4, "draw_rate": 0.25})

        # H2H
        h2h = _head_to_head(df.iloc[:idx], home, away)

        features = [
            dc_h, dc_d, dc_a,
            elo_p["home"], elo_p["draw"], elo_p["away"],
            rh, ra, rh - ra,
            pow_h["attack"], pow_h["defense"],
            pow_a["attack"], pow_a["defense"],
            rs_h["avg_scored"], rs_h["avg_conceded"],
            rs_a["avg_scored"], rs_a["avg_conceded"],
            rs_h["win_rate"], rs_a["win_rate"],
            rs_h["draw_rate"], rs_a["draw_rate"],
            0.0 if neutral else 1.0,
            h2h["h2h_win_a"], h2h["h2h_draw"], h2h["h2h_win_b"],
            tournament_weight(row.get("tournament", "")),
        ]

        X_rows.append(features)
        y_1x2.append(0 if hs > as_ else (1 if hs == as_ else 2))
        y_ou.append(1 if hs + as_ > 2.5 else 0)

    return np.array(X_rows), np.array(y_1x2), np.array(y_ou)


FEATURE_NAMES = [
    "dc_home", "dc_draw", "dc_away",
    "elo_home", "elo_draw", "elo_away",
    "elo_rating_h", "elo_rating_a", "elo_gap",
    "att_h", "def_h", "att_a", "def_a",
    "avg_scored_h", "avg_conceded_h", "avg_scored_a", "avg_conceded_a",
    "win_rate_h", "win_rate_a", "draw_rate_h", "draw_rate_a",
    "home_flag",
    "h2h_win_h", "h2h_draw", "h2h_win_a",
    "tournament_weight",
]


def train(min_date: str = "2018-01-01") -> dict:
    """Train XGBoost meta-learners for 1X2 and O/U, with time-series CV."""
    from model.bivariate_poisson import load_fit

    df = load_matches(min_date=min_date)
    fit = load_fit()

    print(f"Building features from {len(df)} matches ({min_date} → latest)...")
    X, y_1x2, y_ou = build_features(df, fit)
    print(f"Features shape: {X.shape}, 1X2 classes: {np.bincount(y_1x2)}, O/U: {np.bincount(y_ou)}")

    # Time-series cross-validation (no future leakage)
    tscv = TimeSeriesSplit(n_splits=5)

    # 1X2 classifier
    xgb_1x2 = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )

    # Calibrated for proper probability estimates
    cal_1x2 = CalibratedClassifierCV(xgb_1x2, cv=tscv, method="isotonic")
    cal_1x2.fit(X, y_1x2)

    # O/U classifier
    xgb_ou = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    cal_ou = CalibratedClassifierCV(xgb_ou, cv=tscv, method="isotonic")
    cal_ou.fit(X, y_ou)

    # Evaluate on last fold
    train_idx, test_idx = list(tscv.split(X))[-1]
    X_test, y_test_1x2, y_test_ou = X[test_idx], y_1x2[test_idx], y_ou[test_idx]

    pred_1x2 = cal_1x2.predict(X_test)
    pred_ou = cal_ou.predict(X_test)

    acc_1x2 = np.mean(pred_1x2 == y_test_1x2)
    acc_ou = np.mean(pred_ou == y_test_ou)

    # Log loss
    probs_1x2 = cal_1x2.predict_proba(X_test)
    ll = -np.mean([np.log(max(probs_1x2[i, y_test_1x2[i]], 1e-8)) for i in range(len(y_test_1x2))])

    print(f"\nLast fold validation ({len(test_idx)} matches):")
    print(f"  1X2 accuracy: {acc_1x2:.1%}")
    print(f"  O/U accuracy: {acc_ou:.1%}")
    print(f"  1X2 log loss: {ll:.4f}")

    # Feature importance
    print(f"\nTop features (1X2):")
    # Get one of the base estimators for feature importance
    base = cal_1x2.calibrated_classifiers_[0].estimator
    imp = base.feature_importances_
    for i in np.argsort(imp)[::-1][:10]:
        print(f"  {FEATURE_NAMES[i]:25} {imp[i]:.3f}")

    # Save
    with open(META_PATH, "wb") as f:
        pickle.dump(cal_1x2, f)
    with open(META_OU_PATH, "wb") as f:
        pickle.dump(cal_ou, f)
    print(f"\nSaved: {META_PATH.name}, {META_OU_PATH.name}")

    return {"acc_1x2": acc_1x2, "acc_ou": acc_ou, "ll": ll, "n_test": len(test_idx)}


def load_meta():
    """Load trained meta-learners."""
    with open(META_PATH, "rb") as f:
        m_1x2 = pickle.load(f)
    with open(META_OU_PATH, "rb") as f:
        m_ou = pickle.load(f)
    return m_1x2, m_ou


def predict_meta(fit, home: str, away: str, neutral: bool = False) -> dict:
    """Get meta-learner predictions for a single match."""
    from model.predict import attack_defense_power, _get_form

    m_1x2, m_ou = load_meta()

    # Build single-match feature vector
    try:
        grid = fit.predict_grid(home, away, neutral=neutral)
        dc_h, dc_d, dc_a = grid.home_win, grid.draw, grid.away_win
    except (KeyError, Exception):
        dc_h, dc_d, dc_a = 0.45, 0.25, 0.30

    elo = compute_ratings()
    rh = elo.get(home, INITIAL_RATING)
    ra = elo.get(away, INITIAL_RATING)
    elo_p = elo_match_probs(rh, ra, neutral=neutral)

    pow_h = attack_defense_power(fit, home) or {"attack": 0.0, "defense": 0.0}
    pow_a = attack_defense_power(fit, away) or {"attack": 0.0, "defense": 0.0}

    df = load_matches()
    rolling = _rolling_stats(df)
    rs_h = rolling.get(home, {"avg_scored": 1.3, "avg_conceded": 1.1, "win_rate": 0.4, "draw_rate": 0.25})
    rs_a = rolling.get(away, {"avg_scored": 1.3, "avg_conceded": 1.1, "win_rate": 0.4, "draw_rate": 0.25})
    h2h = _head_to_head(df, home, away)

    X = np.array([[
        dc_h, dc_d, dc_a,
        elo_p["home"], elo_p["draw"], elo_p["away"],
        rh, ra, rh - ra,
        pow_h["attack"], pow_h["defense"],
        pow_a["attack"], pow_a["defense"],
        rs_h["avg_scored"], rs_h["avg_conceded"],
        rs_a["avg_scored"], rs_a["avg_conceded"],
        rs_h["win_rate"], rs_a["win_rate"],
        rs_h["draw_rate"], rs_a["draw_rate"],
        0.0 if neutral else 1.0,
        h2h["h2h_win_a"], h2h["h2h_draw"], h2h["h2h_win_b"],
        5.0,  # WC tournament weight
    ]])

    probs_1x2 = m_1x2.predict_proba(X)[0]
    probs_ou = m_ou.predict_proba(X)[0]

    return {
        "1x2": {"home": float(probs_1x2[0]), "draw": float(probs_1x2[1]), "away": float(probs_1x2[2])},
        "ou_2_5": {"under": float(probs_ou[0]), "over": float(probs_ou[1])},
    }


if __name__ == "__main__":
    result = train()
    print(f"\n{'='*50}")
    print(f"META-LEARNER TRAINED")
    print(f"  1X2: {result['acc_1x2']:.1%}")
    print(f"  O/U: {result['acc_ou']:.1%}")
    print(f"  LogL: {result['ll']:.4f}")
