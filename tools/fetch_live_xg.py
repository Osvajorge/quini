"""Fetch live xG from api-football.com for in-progress WC2026 fixtures.

Merges real shot-tracking xG into predictions.json so the live heatmap
and conditional probability bars use real data instead of pre-match lambda.

Logic:
- Only runs if there are is_live fixtures in predictions.json
- Calls /fixtures/statistics once per live fixture (≤8 calls/day for WC)
- Updates xg_home / xg_away with remaining expected goals estimate
  Formula: remaining_xg = observed_xg / elapsed * (90 - elapsed)
- Safe to run repeatedly — no-ops if no live matches

Run:   python -m tools.fetch_live_xg
Env:   API_FOOTBALL_KEY=<your key>
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PRED = ROOT / "docs" / "data" / "predictions.json"

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}


def _get(path: str) -> dict:
    r = requests.get(BASE + path, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _remaining_xg(observed: float, elapsed: int) -> float:
    """Estimate remaining xG from observed xG rate and time left."""
    if elapsed <= 0:
        return observed
    rate = observed / elapsed
    remaining_min = max(0, 90 - elapsed)
    return round(rate * remaining_min, 3)


def fetch() -> None:
    if not API_KEY:
        print("[live_xg] API_FOOTBALL_KEY not set — skipping")
        return

    if not PRED.exists():
        print("[live_xg] no predictions.json — skipping")
        return

    with open(PRED) as f:
        data = json.load(f)
    fixtures = data.get("fixtures", [])

    live = [f for f in fixtures if f.get("is_live") and not f.get("completed")]
    if not live:
        print("[live_xg] no live fixtures — nothing to do")
        return

    print(f"[live_xg] {len(live)} live fixture(s) — fetching stats")

    # Get today's WC fixtures from api-football to find fixture IDs
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_data = _get(f"/fixtures?date={today}")
    wc_today = {
        (
            _norm(fx["teams"]["home"]["name"]),
            _norm(fx["teams"]["away"]["name"]),
        ): fx
        for fx in today_data.get("response", [])
        if fx.get("league", {}).get("name") == "World Cup"
    }

    if not wc_today:
        print("[live_xg] no WC fixtures found in api-football for today")
        return

    updated = 0
    for f in live:
        home_n = _norm(f["home"])
        away_n = _norm(f["away"])

        apif = wc_today.get((home_n, away_n))
        if not apif:
            # Try reversed (home/away swap edge cases)
            apif = wc_today.get((away_n, home_n))
            if apif:
                # Swap home/away for stats lookup
                apif = dict(apif)

        if not apif:
            print(f"[live_xg] no api-football match for {f['home']} vs {f['away']}")
            continue

        fid = apif["fixture"]["id"]
        elapsed = apif["fixture"]["status"].get("elapsed") or 0
        elapsed = int(elapsed)

        if elapsed <= 0:
            print(f"[live_xg] {f['home']} vs {f['away']} — elapsed=0, skipping")
            continue

        # Fetch statistics
        stats_data = _get(f"/fixtures/statistics?fixture={fid}")
        if not stats_data.get("results"):
            print(f"[live_xg] no stats yet for fixture {fid}")
            continue

        xg_home_obs = None
        xg_away_obs = None

        for team_stats in stats_data["response"]:
            team_name = team_stats["team"]["name"]
            stats_dict = {s["type"]: s["value"] for s in team_stats["statistics"]}
            xg_val = stats_dict.get("expected_goals")
            try:
                xg_float = float(xg_val) if xg_val is not None else None
            except (ValueError, TypeError):
                xg_float = None

            if _norm(team_name) == home_n:
                xg_home_obs = xg_float
            else:
                xg_away_obs = xg_float

        if xg_home_obs is None or xg_away_obs is None:
            print(f"[live_xg] xG not found in stats for {f['home']} vs {f['away']}")
            continue

        # Store observed xG and compute remaining
        f["xg_home_observed"] = xg_home_obs
        f["xg_away_observed"] = xg_away_obs
        f["xg_home"] = _remaining_xg(xg_home_obs, elapsed)
        f["xg_away"] = _remaining_xg(xg_away_obs, elapsed)
        f["xg_elapsed"] = elapsed
        f["xg_source"] = "api-football"

        print(
            f"[live_xg] {f['home']} vs {f['away']} min={elapsed}' "
            f"observed=({xg_home_obs:.2f}/{xg_away_obs:.2f}) "
            f"remaining=({f['xg_home']:.2f}/{f['xg_away']:.2f})"
        )
        updated += 1

    if updated:
        data["xg_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp = PRED.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.rename(PRED)
        print(f"[live_xg] updated {updated} fixture(s) in predictions.json")
    else:
        print("[live_xg] no xG updates applied")


def _norm(name: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFD", (name or "").lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.replace(" ", "").replace(".", "").replace("'", "").replace("-", "")
    aliases = {
        "unitedstates": "usa", "usmensnationalteam": "usa",
        "korearepublic": "southkorea", "republicofireland": "ireland",
        "czechia": "czechrepublic", "ivorycoast": "cotedivoire",
        "curacao": "curacao",
    }
    return aliases.get(n, n)


if __name__ == "__main__":
    fetch()
