"""Align The Odds API events with martj42 matches.

Resolves: team name mismatches (e.g. 'Czechia' ↔ 'Czech Republic'), timezone
edge cases that push commence_time across midnight UTC. Returns a DataFrame
joining odds + martj42 outcomes for the ROI backtest.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from model.data_loader import load_matches

ODDS_CSV = Path(__file__).resolve().parent.parent / "data" / "odds_history.csv"

# Map of odds-api team name → martj42 canonical name
TEAM_ALIASES = {
    "Czechia": "Czech Republic",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Ireland": "Republic of Ireland",
    "USA": "United States",
    "Türkiye": "Turkey",
    "South Korea": "Korea Republic",
    "North Korea": "Korea DPR",
    "Republic of North Macedonia": "North Macedonia",
    "Cape Verde Islands": "Cape Verde",
}


def canonical(team: str) -> str:
    return TEAM_ALIASES.get(team, team)


def load_aligned(odds_csv: Path = ODDS_CSV) -> pd.DataFrame:
    """Return odds rows joined to martj42 matches with actual scores.

    Output columns: [date, home_team, away_team, neutral, tournament,
                     home_score, away_score, h2h_*, spread_*, bookmaker, sport_key]
    """
    odds = pd.read_csv(odds_csv)
    odds["home_team"] = odds["home_team"].map(canonical)
    odds["away_team"] = odds["away_team"].map(canonical)
    odds["odds_date"] = pd.to_datetime(odds["date"])

    mart = load_matches()
    mart = mart[(mart["date"] >= "2024-01-01") & (mart["date"] <= "2026-07-01")].copy()

    joined: list[dict] = []
    for _, o in odds.iterrows():
        target_date = o["odds_date"]
        # Allow ±1 day tolerance for timezone effects.
        for delta in (0, -1, 1):
            d = target_date + pd.Timedelta(days=delta)
            sub = mart[
                (mart["date"] == d)
                & (mart["home_team"] == o["home_team"])
                & (mart["away_team"] == o["away_team"])
            ]
            if len(sub):
                m = sub.iloc[0]
                joined.append({
                    "date": m["date"],
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "neutral": bool(m["neutral"]),
                    "tournament": m["tournament"],
                    "home_score": int(m["home_score"]),
                    "away_score": int(m["away_score"]),
                    "h2h_home": o["h2h_home"],
                    "h2h_draw": o["h2h_draw"],
                    "h2h_away": o["h2h_away"],
                    "spread_home_handicap": o["spread_home_handicap"],
                    "spread_home_price": o["spread_home_price"],
                    "spread_away_handicap": o["spread_away_handicap"],
                    "spread_away_price": o["spread_away_price"],
                    "bookmaker": o["bookmaker"],
                    "sport_key": o["sport_key"],
                    "event_id": o["event_id"],
                })
                break

    df = pd.DataFrame(joined)
    return df.drop_duplicates(subset=["event_id"]).reset_index(drop=True)


if __name__ == "__main__":
    df = load_aligned()
    print(f"aligned events: {len(df)}")
    print(f"date range: {df.date.min().date()} → {df.date.max().date()}")
    print(f"\ntournament coverage:")
    print(df.tournament.value_counts().to_string())
    print(f"\nh2h triple complete: {df[['h2h_home','h2h_draw','h2h_away']].dropna().shape[0]}")
    print(f"AH ±1.5 available: {((df.spread_home_handicap.abs() == 1.5)).sum()}")
    print(f"\nbookmaker breakdown:")
    print(df.bookmaker.value_counts().head(10).to_string())
