"""StatsBomb WC2022 open-data → per-player tournament aggregates.

Pulls all 64 WC2022 matches, aggregates: goals, xG, shots, shots_on_target,
key_passes, completed_passes, total_passes. Cache to player-level + team-level
JSON files. One-time run (WC2022 is static).

Output:
  docs/data/statsbomb_wc2022_players.json
  docs/data/statsbomb_wc2022_teams.json

Run: python -m tools.fetch_statsbomb [--force]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PLAYERS = ROOT / "docs" / "data" / "statsbomb_wc2022_players.json"
OUT_TEAMS = ROOT / "docs" / "data" / "statsbomb_wc2022_teams.json"

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
WC2022_COMP_ID = 43
WC2022_SEASON_ID = 106


def _get(url: str) -> list | dict:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def aggregate(force: bool = False) -> None:
    if OUT_PLAYERS.exists() and not force:
        print(f"[sb] cache exists at {OUT_PLAYERS.name} — use --force to rebuild")
        return

    print("[sb] fetching WC2022 match list...")
    matches = _get(f"{BASE}/matches/{WC2022_COMP_ID}/{WC2022_SEASON_ID}.json")
    print(f"[sb] {len(matches)} matches")

    # player_key → totals
    players = defaultdict(lambda: {
        "name": "", "team": "", "country": "",
        "goals": 0, "xg": 0.0, "shots": 0, "shots_on_target": 0,
        "key_passes": 0, "passes": 0, "passes_completed": 0,
        "tackles": 0, "interceptions": 0, "dribbles": 0, "carries": 0,
        "yellow_cards": 0, "red_cards": 0,
        "minutes": 0, "matches": set(),
    })

    teams = defaultdict(lambda: {
        "team": "", "matches": set(),
        "goals_for": 0, "goals_against": 0,
        "xg_for": 0.0, "xg_against": 0.0,
        "shots_for": 0, "shots_against": 0,
        "wins": 0, "draws": 0, "losses": 0,
    })

    for i, m in enumerate(matches, 1):
        mid = m["match_id"]
        home = m["home_team"]["home_team_name"]
        away = m["away_team"]["away_team_name"]
        hs = m["home_score"]
        as_ = m["away_score"]
        # Team totals
        teams[home]["team"] = home
        teams[home]["matches"].add(mid)
        teams[home]["goals_for"] += hs
        teams[home]["goals_against"] += as_
        teams[away]["team"] = away
        teams[away]["matches"].add(mid)
        teams[away]["goals_for"] += as_
        teams[away]["goals_against"] += hs
        if hs > as_:
            teams[home]["wins"] += 1; teams[away]["losses"] += 1
        elif hs < as_:
            teams[away]["wins"] += 1; teams[home]["losses"] += 1
        else:
            teams[home]["draws"] += 1; teams[away]["draws"] += 1

        # Pull events for player + xG aggregation
        try:
            events = _get(f"{BASE}/events/{mid}.json")
        except Exception as e:
            print(f"[sb] {i}/{len(matches)} {mid} fetch failed: {e}")
            continue

        print(f"[sb] {i}/{len(matches)} {home} vs {away}  events={len(events)}")

        for ev in events:
            etype = ev.get("type", {}).get("name", "")
            team = (ev.get("team") or {}).get("name", "")
            player = ev.get("player") or {}
            pid = player.get("id")
            pname = player.get("name", "")

            if not pid and etype not in ("Shot",):
                continue
            key = f"{pid}::{pname}" if pid else pname
            if key:
                p = players[key]
                p["name"] = pname or p["name"]
                p["team"] = team or p["team"]
                p["matches"].add(mid)

            if etype == "Shot" and "shot" in ev:
                s = ev["shot"]
                xg = s.get("statsbomb_xg", 0) or 0
                outcome = (s.get("outcome") or {}).get("name", "")
                is_goal = outcome == "Goal"
                is_on_target = outcome in ("Goal", "Saved", "Saved To Post", "Saved Off T")
                if key:
                    p["shots"] += 1
                    p["xg"] += float(xg)
                    if is_goal:
                        p["goals"] += 1
                    if is_on_target:
                        p["shots_on_target"] += 1
                if team:
                    teams[team]["shots_for"] += 1
                    teams[team]["xg_for"] += float(xg)
                    other = away if team == home else home
                    teams[other]["shots_against"] += 1
                    teams[other]["xg_against"] += float(xg)

            elif etype == "Pass":
                if not key:
                    continue
                p["passes"] += 1
                ps = ev.get("pass") or {}
                if not (ps.get("outcome") or {}).get("name"):  # successful pass = no outcome
                    p["passes_completed"] += 1
                if ps.get("shot_assist") or ps.get("goal_assist"):
                    p["key_passes"] += 1

            elif etype == "Duel" and key:
                p["tackles"] += 1
            elif etype == "Interception" and key:
                p["interceptions"] += 1
            elif etype == "Dribble" and key:
                p["dribbles"] += 1
            elif etype == "Carry" and key:
                p["carries"] += 1
            elif etype == "Foul Committed" and key:
                card = (ev.get("foul_committed") or {}).get("card") or {}
                if "Yellow" in (card.get("name") or ""):
                    p["yellow_cards"] += 1
                elif "Red" in (card.get("name") or ""):
                    p["red_cards"] += 1

    # Serialize: convert match-sets to counts
    players_out = []
    for key, p in players.items():
        if not p["name"]:
            continue
        n_matches = len(p["matches"])
        if n_matches == 0:
            continue
        players_out.append({
            "player": p["name"],
            "team": p["team"],
            "matches": n_matches,
            "goals": p["goals"],
            "xg": round(p["xg"], 3),
            "shots": p["shots"],
            "shots_on_target": p["shots_on_target"],
            "key_passes": p["key_passes"],
            "passes": p["passes"],
            "passes_completed": p["passes_completed"],
            "pass_pct": round(p["passes_completed"] / p["passes"] * 100, 1) if p["passes"] else 0,
            "tackles": p["tackles"],
            "interceptions": p["interceptions"],
            "dribbles": p["dribbles"],
            "carries": p["carries"],
            "yellow_cards": p["yellow_cards"],
            "red_cards": p["red_cards"],
            "xg_per_shot": round(p["xg"] / p["shots"], 3) if p["shots"] else 0,
            "goals_minus_xg": round(p["goals"] - p["xg"], 2),
        })
    players_out.sort(key=lambda x: (-x["goals"], -x["xg"]))

    teams_out = []
    for name, t in teams.items():
        n_matches = len(t["matches"])
        teams_out.append({
            "team": name,
            "matches": n_matches,
            "wins": t["wins"], "draws": t["draws"], "losses": t["losses"],
            "goals_for": t["goals_for"], "goals_against": t["goals_against"],
            "xg_for": round(t["xg_for"], 2), "xg_against": round(t["xg_against"], 2),
            "shots_for": t["shots_for"], "shots_against": t["shots_against"],
            "xg_diff": round(t["xg_for"] - t["xg_against"], 2),
        })
    teams_out.sort(key=lambda x: -x["xg_diff"])

    OUT_PLAYERS.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"players": players_out, "source": "StatsBomb open-data WC2022"},
              open(OUT_PLAYERS, "w"), indent=2, ensure_ascii=False)
    json.dump({"teams": teams_out, "source": "StatsBomb open-data WC2022"},
              open(OUT_TEAMS, "w"), indent=2, ensure_ascii=False)
    print(f"[sb] saved {len(players_out)} players to {OUT_PLAYERS.name}")
    print(f"[sb] saved {len(teams_out)} teams to {OUT_TEAMS.name}")
    print(f"[sb] top 5 scorers WC2022:")
    for p in players_out[:5]:
        print(f"  {p['player']:30} ({p['team']}) — G={p['goals']} xG={p['xg']:.2f} shots={p['shots']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    aggregate(force=args.force)
