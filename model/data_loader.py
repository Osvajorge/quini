"""Load and weight martj42 international results for Dixon-Coles fit."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "results.csv"
HALF_LIFE_DAYS = 180
MIN_DATE = "2005-01-01"

CONTINENTAL = {
    "UEFA Euro",
    "Copa América",
    "African Cup of Nations",
    "AFC Asian Cup",
    "Gold Cup",
    "AFC Challenge Cup",
}


def tournament_weight(name: str) -> float:
    if not isinstance(name, str):
        return 1.0
    if "qualification" in name:
        return 3.0
    if "Nations League" in name:
        return 3.5
    if "FIFA World Cup" in name:
        return 5.0
    if name in CONTINENTAL:
        return 4.0
    if name == "Friendly":
        return 1.0
    return 2.0


def time_weight(days_ago: np.ndarray, half_life: float = HALF_LIFE_DAYS) -> np.ndarray:
    return np.exp(-np.log(2) * days_ago / half_life)


def load_matches(
    path: Path = DATA_PATH,
    min_date: str = MIN_DATE,
    as_of: datetime | None = None,
    half_life: float = HALF_LIFE_DAYS,
) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    as_of = as_of or df["date"].max()
    if isinstance(as_of, str):
        as_of = pd.Timestamp(as_of)

    df = df[df["date"] >= pd.Timestamp(min_date)]
    df = df[df["date"] <= as_of]
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)
    df["neutral"] = df["neutral"].astype(bool) if df["neutral"].dtype == bool else df["neutral"].astype(str).str.upper().eq("TRUE")

    days_ago = (as_of - df["date"]).dt.days.to_numpy()
    df["w_time"] = time_weight(days_ago, half_life)
    df["w_tournament"] = df["tournament"].map(tournament_weight)
    df["weight"] = df["w_time"] * df["w_tournament"]

    return df[
        ["date", "home_team", "away_team", "home_score", "away_score", "neutral", "tournament", "weight"]
    ].reset_index(drop=True)


def team_match_counts(df: pd.DataFrame) -> pd.Series:
    """Recent match count per team — proxy for confidence in parameters."""
    home = df.groupby("home_team").size()
    away = df.groupby("away_team").size()
    return home.add(away, fill_value=0).astype(int).sort_values(ascending=False)


if __name__ == "__main__":
    df = load_matches()
    print(f"matches: {len(df)}")
    print(f"date range: {df.date.min().date()} → {df.date.max().date()}")
    print(f"teams: {df.home_team.nunique()} home / {df.away_team.nunique()} away")
    print(f"weight sum: {df.weight.sum():.1f}")
    print(f"weight mean: {df.weight.mean():.3f}")
    print()
    print("weight by tournament bucket:")
    print(df.groupby("tournament")["weight"].mean().sort_values(ascending=False).head(10))
    print()
    print("top 10 teams by recent matches:")
    print(team_match_counts(df).head(10))
