"""Pull historical closing odds from The Odds API and align with martj42 matches.

Usage:
  # Pilot: small scope to validate pipeline + cost
  python -m data.fetch_odds --pilot

  # Full pull for backtest
  python -m data.fetch_odds --full

Output: data/odds_history.csv with columns
  date, sport_key, home_team, away_team, commence_time,
  h2h_home, h2h_draw, h2h_away,
  spread_home_handicap, spread_home_price, spread_away_handicap, spread_away_price,
  bookmaker, snapshot_timestamp
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from model.config import THE_ODDS_API_KEY_PAID, require
from model.data_loader import load_matches

BASE = "https://api.the-odds-api.com/v4"
ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = ROOT / "data" / "odds_history.csv"

# martj42 tournament name → list of The Odds API sport_ids
TOURNAMENT_TO_SPORTS = {
    "UEFA Nations League": ["soccer_uefa_nations_league"],
    "UEFA Euro qualification": ["soccer_uefa_euro_qualification"],
    "UEFA Euro": ["soccer_uefa_european_championship"],
    "Copa América": ["soccer_conmebol_copa_america"],
    "Gold Cup": ["soccer_concacaf_gold_cup"],
    "FIFA World Cup": ["soccer_fifa_world_cup"],
    # quals come from both UEFA + CONMEBOL; try both
    "FIFA World Cup qualification": [
        "soccer_fifa_world_cup_qualifiers_europe",
        "soccer_fifa_world_cup_qualifiers_south_america",
    ],
}

MARKETS = "h2h,spreads"  # 20 credits/call
REGIONS = "eu"
ODDS_FORMAT = "decimal"
SNAPSHOT_HOUR_UTC = 18  # most international kickoffs are evening UTC


def _norm_team(name: str) -> str:
    return (name or "").lower().strip().replace(".", "").replace("-", " ")


def fetch_snapshot(sport: str, snapshot_iso: str, api_key: str) -> dict | None:
    r = requests.get(
        f"{BASE}/historical/sports/{sport}/odds",
        params={
            "apiKey": api_key,
            "regions": REGIONS,
            "markets": MARKETS,
            "oddsFormat": ODDS_FORMAT,
            "date": snapshot_iso,
        },
        timeout=20,
    )
    cost = int(r.headers.get("x-requests-last", 0) or 0)
    remaining = int(r.headers.get("x-requests-remaining", 0) or 0)
    print(
        f"  GET {sport} @ {snapshot_iso[:10]}  ·  status={r.status_code}  cost={cost}  remaining={remaining}"
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def best_bookmaker_consensus(event: dict) -> dict | None:
    """Pick odds from the bookmaker with most complete coverage for h2h + spreads."""
    best = None
    best_score = -1
    for bm in event.get("bookmakers", []):
        markets = {m["key"]: m for m in bm.get("markets", [])}
        score = 0
        if "h2h" in markets and len(markets["h2h"]["outcomes"]) >= 2:
            score += 2
        if "spreads" in markets and len(markets["spreads"]["outcomes"]) >= 2:
            score += 1
        if score > best_score:
            best_score = score
            best = bm
    return best


def extract_odds(event: dict) -> dict | None:
    bm = best_bookmaker_consensus(event)
    if bm is None:
        return None
    markets = {m["key"]: m for m in bm.get("markets", [])}

    row = {
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time"),
        "bookmaker": bm.get("key"),
        "h2h_home": None, "h2h_draw": None, "h2h_away": None,
        "spread_home_handicap": None, "spread_home_price": None,
        "spread_away_handicap": None, "spread_away_price": None,
    }

    if "h2h" in markets:
        for o in markets["h2h"]["outcomes"]:
            if o["name"] == event["home_team"]:
                row["h2h_home"] = o["price"]
            elif o["name"] == event["away_team"]:
                row["h2h_away"] = o["price"]
            else:
                row["h2h_draw"] = o["price"]

    if "spreads" in markets:
        for o in markets["spreads"]["outcomes"]:
            if o["name"] == event["home_team"]:
                row["spread_home_handicap"] = o.get("point")
                row["spread_home_price"] = o["price"]
            elif o["name"] == event["away_team"]:
                row["spread_away_handicap"] = o.get("point")
                row["spread_away_price"] = o["price"]

    return row


def plan_calls(
    holdout_start: str,
    holdout_end: str,
    tournament_whitelist: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Return list of (sport_id, snapshot_iso) calls to make, deduped."""
    df = load_matches()
    df = df[(df["date"] >= pd.Timestamp(holdout_start)) & (df["date"] <= pd.Timestamp(holdout_end))]
    if tournament_whitelist:
        df = df[df["tournament"].isin(tournament_whitelist)]
    df = df[df["tournament"].isin(TOURNAMENT_TO_SPORTS.keys())]

    pairs: set[tuple[str, str]] = set()
    for _, m in df.iterrows():
        sports = TOURNAMENT_TO_SPORTS[m["tournament"]]
        snap = m["date"].replace(hour=SNAPSHOT_HOUR_UTC, minute=0, second=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for s in sports:
            pairs.add((s, snap))
    return sorted(pairs, key=lambda p: (p[1], p[0]))


def run(
    holdout_start: str,
    holdout_end: str,
    tournament_whitelist: list[str] | None = None,
    dry_run: bool = False,
    yes: bool = False,
    out: Path = OUT_CSV,
) -> None:
    api_key = require("THE_ODDS_API_KEY_PAID", THE_ODDS_API_KEY_PAID)
    calls = plan_calls(holdout_start, holdout_end, tournament_whitelist)
    est_cost = len(calls) * 20  # h2h + spreads × 1 region × 10x multiplier

    print(f"planned calls: {len(calls)}")
    print(f"estimated cost: {est_cost} credits (h2h + spreads, 1 region)")

    if dry_run:
        print("\nFirst 10 calls:")
        for c in calls[:10]:
            print(f"  {c[0]:50}  @ {c[1]}")
        return

    # Confirm if cost > 1500 credits and not in non-interactive mode
    if est_cost > 1500 and not yes:
        ans = input(f"This will spend ~{est_cost} of your 20K credits. Proceed? [y/N] ")
        if ans.strip().lower() != "y":
            print("aborted.")
            return

    rows: list[dict] = []
    seen_events: dict[str, datetime] = {}  # event_id → snapshot used

    for i, (sport, snap_iso) in enumerate(calls, 1):
        print(f"\n[{i}/{len(calls)}]", end="")
        try:
            payload = fetch_snapshot(sport, snap_iso, api_key)
        except requests.HTTPError as e:
            print(f"  HTTP error: {e}")
            continue
        if payload is None:
            continue

        events = payload.get("data", []) if isinstance(payload, dict) else []
        snapshot_ts = payload.get("timestamp") if isinstance(payload, dict) else snap_iso

        for ev in events:
            ev_id = ev.get("id")
            if not ev_id:
                continue
            commence = ev.get("commence_time", "")
            # Keep the snapshot closest to commence_time per event
            prev = seen_events.get(ev_id)
            this_dt = datetime.fromisoformat(snapshot_ts.replace("Z", "+00:00"))
            if prev and this_dt <= prev:
                continue
            seen_events[ev_id] = this_dt

            row = extract_odds(ev)
            if row is None:
                continue
            row["event_id"] = ev_id
            row["sport_key"] = sport
            row["snapshot_timestamp"] = snapshot_ts
            row["date"] = commence[:10]
            rows.append(row)

        time.sleep(0.2)  # courtesy throttle

    # Deduplicate: keep latest snapshot per event_id
    final = {}
    for r in rows:
        existing = final.get(r["event_id"])
        if existing is None or r["snapshot_timestamp"] > existing["snapshot_timestamp"]:
            final[r["event_id"]] = r

    print(f"\nunique events captured: {len(final)}")

    if final:
        out.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "date", "sport_key", "event_id", "home_team", "away_team", "commence_time",
            "h2h_home", "h2h_draw", "h2h_away",
            "spread_home_handicap", "spread_home_price",
            "spread_away_handicap", "spread_away_price",
            "bookmaker", "snapshot_timestamp",
        ]
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in final.values():
                w.writerow({k: r.get(k) for k in fieldnames})
        print(f"saved → {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pilot", action="store_true", help="Nations League 2024 only (~40 calls)")
    p.add_argument("--full", action="store_true", help="Full holdout 2024-2026")
    p.add_argument("--dry-run", action="store_true", help="Plan calls without spending credits")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p.add_argument("--out", type=Path, default=OUT_CSV)
    args = p.parse_args()

    if not (args.pilot or args.full or args.dry_run):
        p.error("Specify --pilot, --full, or --dry-run")

    if args.pilot:
        run(
            holdout_start="2024-09-01",
            holdout_end="2024-12-31",
            tournament_whitelist=["UEFA Nations League"],
            dry_run=args.dry_run,
            yes=args.yes,
            out=args.out.parent / "odds_pilot.csv" if args.out == OUT_CSV else args.out,
        )
    elif args.full:
        run(
            holdout_start="2024-01-01",
            holdout_end="2026-06-17",
            tournament_whitelist=None,
            dry_run=args.dry_run,
            yes=args.yes,
            out=args.out,
        )
