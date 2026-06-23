"""Elo ratings for national teams — used as regularizer in ensemble.

Computed incrementally from results.csv. Combines with BivariatePoisson
in predict.py via linear blend of 1X2 probabilities.

Key decisions:
- K-factor varies by tournament (friendlies low, World Cup high)
- Home advantage = 65 Elo points (~10% win prob bump at parity)
- Goal-difference multiplier: bigger wins move ratings more
- Initial rating: 1500
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "results.csv"

INITIAL_RATING = 1500.0
HOME_ADVANTAGE = 65.0  # Elo pts (~10% win-prob bump at parity)

K_BY_TOURNAMENT = {
    "FIFA World Cup": 60.0,
    "UEFA Euro": 50.0,
    "Copa América": 50.0,
    "African Cup of Nations": 50.0,
    "AFC Asian Cup": 50.0,
    "Gold Cup": 45.0,
    "Nations League": 35.0,
    "FIFA World Cup qualification": 40.0,
    "Friendly": 20.0,
}


def _k_factor(tournament: str | None) -> float:
    if not isinstance(tournament, str):
        return 30.0
    for key, k in K_BY_TOURNAMENT.items():
        if key in tournament:
            return k
    return 30.0  # default for other competitive


def _goal_diff_multiplier(home_score: int, away_score: int, rating_diff: float) -> float:
    """FIFA-style multiplier — larger margins move Elo more.

    1.0 for diff=1, 1.5 for diff=2, log scaling after that.
    Capped to avoid extreme blowouts dominating.
    """
    gd = abs(home_score - away_score)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    # log scale for 3+
    return (11.0 + gd) / 8.0


def _expected_score(rating_a: float, rating_b: float) -> float:
    """Expected score (0-1) for team A vs B in Elo."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def compute_ratings(
    df: Optional[pd.DataFrame] = None,
    as_of: Optional[datetime] = None,
) -> dict[str, float]:
    """Run Elo over all matches in df, return team→rating dict.

    Uses the full historical dataset by default. Stable: same result for
    same inputs.
    """
    if df is None:
        df = pd.read_csv(DATA_PATH, parse_dates=["date"])

    df = df.dropna(subset=["home_score", "away_score"]).copy()
    if as_of is not None:
        df = df[df["date"] <= pd.Timestamp(as_of)]
    df = df.sort_values("date").reset_index(drop=True)

    ratings: dict[str, float] = defaultdict(lambda: INITIAL_RATING)
    home_scores = df["home_score"].astype(int).to_numpy()
    away_scores = df["away_score"].astype(int).to_numpy()
    home_teams = df["home_team"].to_numpy()
    away_teams = df["away_team"].to_numpy()
    tournaments = df["tournament"].to_numpy()
    neutrals = (
        df["neutral"].astype(str).str.upper().eq("TRUE")
        if df["neutral"].dtype == object
        else df["neutral"].astype(bool)
    ).to_numpy()

    for i in range(len(df)):
        h, a = home_teams[i], away_teams[i]
        hs, as_ = int(home_scores[i]), int(away_scores[i])
        neutral = bool(neutrals[i])
        tournament = tournaments[i]

        rh, ra = ratings[h], ratings[a]
        adv = 0.0 if neutral else HOME_ADVANTAGE
        exp_h = _expected_score(rh + adv, ra)

        if hs > as_:
            actual_h = 1.0
        elif hs == as_:
            actual_h = 0.5
        else:
            actual_h = 0.0

        k = _k_factor(tournament)
        mult = _goal_diff_multiplier(hs, as_, (rh + adv) - ra)
        delta = k * mult * (actual_h - exp_h)
        ratings[h] = rh + delta
        ratings[a] = ra - delta

    return dict(ratings)


def match_probs(
    rating_home: float,
    rating_away: float,
    neutral: bool = False,
    draw_factor: float = 0.28,
) -> dict[str, float]:
    """Convert two Elo ratings into 1X2 probabilities.

    draw_factor: empirically ~28% of intl matches end in draws when teams
    are evenly rated. Distributed away from win/loss based on closeness.
    """
    adv = 0.0 if neutral else HOME_ADVANTAGE
    diff = (rating_home + adv) - rating_away
    p_home_no_draw = 1.0 / (1.0 + 10 ** (-diff / 400.0))

    # Draw probability: highest when teams are equal, lower when mismatch
    # Approximate via gaussian-like centered on diff=0
    closeness = math.exp(-(diff ** 2) / (2 * 200 ** 2))
    p_draw = draw_factor * closeness

    # Renormalize win probabilities to (1 - p_draw)
    p_home = p_home_no_draw * (1.0 - p_draw)
    p_away = (1.0 - p_home_no_draw) * (1.0 - p_draw)

    s = p_home + p_draw + p_away
    return {"home": p_home / s, "draw": p_draw / s, "away": p_away / s}


def predict(home: str, away: str, neutral: bool = False, ratings: dict | None = None) -> dict:
    """1X2 probabilities for a future match."""
    if ratings is None:
        ratings = compute_ratings()
    rh = ratings.get(home, INITIAL_RATING)
    ra = ratings.get(away, INITIAL_RATING)
    return {
        "ratings": {"home": round(rh, 1), "away": round(ra, 1)},
        "probs": match_probs(rh, ra, neutral),
    }


if __name__ == "__main__":
    ratings = compute_ratings()
    top = sorted(ratings.items(), key=lambda x: -x[1])[:20]
    print("Top 20 Elo:")
    for team, r in top:
        print(f"  {team:25} {r:7.1f}")
    print()
    print("Brazil vs Scotland (neutral):")
    print(predict("Brazil", "Scotland", neutral=True, ratings=ratings))
