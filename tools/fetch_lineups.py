"""Fetch confirmed lineups for upcoming and live fixtures from ESPN.

Writes docs/data/lineups.json keyed by fixture_id with:
  - home/away starting XI (name, jersey, position)
  - formation per team
  - notable absences (players who appeared in previous matches but not starting)
  - fetched_at timestamp

Run 1-2h before kickoff: python -m tools.fetch_lineups
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PRED   = ROOT / "docs" / "data" / "predictions.json"
STATS  = ROOT / "docs" / "data" / "match_stats.json"
OUT    = ROOT / "docs" / "data" / "lineups.json"

ESPN_SB  = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_SUM = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"


def _norm(name: str) -> str:
    n = unicodedata.normalize("NFD", (name or "").lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.replace(" ", "").replace(".", "").replace("'", "").replace("-", "")


_ALIASES = {
    "unitedstates": "usa", "unitedstatesmennationalteam": "usa",
    "korearepublic": "southkorea", "republicofireland": "ireland",
    "democraticrepublicofcongo": "drcongo", "capeverde": "capeverde",
    "costarica": "costarica", "saudiarabia": "saudiarabia",
    "newzealand": "newzealand",
}


def _norm_alias(name: str) -> str:
    k = _norm(name)
    return _ALIASES.get(k, k)


def _load_known_players() -> dict[str, set[str]]:
    """team → set of player display names who appeared in previous matches."""
    if not STATS.exists():
        return {}
    data = json.load(open(STATS))
    known: dict[str, set[str]] = {}
    for fid, fx in data.items():
        for L in fx.get("leaders", []):
            team = L.get("team", "")
            player = L.get("player", "")
            if team and player:
                known.setdefault(team, set()).add(player)
    return known


def fetch_scoreboard_dates(dates: set[str]) -> dict:
    """Returns {(home_norm, away_norm, date): espn_event_id}."""
    mapping = {}
    for d in dates:
        try:
            r = requests.get(ESPN_SB, params={"dates": d.replace("-", "")}, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"[lineups] scoreboard failed {d}: {e}")
            continue
        for ev in r.json().get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            home = away = None
            for c in comp.get("competitors", []):
                t = (c.get("team") or {}).get("displayName", "")
                if c.get("homeAway") == "home": home = t
                elif c.get("homeAway") == "away": away = t
            if home and away:
                date = (ev.get("date") or "")[:10]
                mapping[(_norm_alias(home), _norm_alias(away), date)] = ev["id"]
    return mapping


def _extract_roster(team_block: dict) -> dict:
    team_name = team_block.get("team", {}).get("displayName", "")
    formation = team_block.get("formation", "")
    roster = team_block.get("roster", [])
    starters, subs = [], []
    for p in roster:
        athlete = p.get("athlete", {})
        entry = {
            "name": athlete.get("displayName", ""),
            "short": athlete.get("shortName", ""),
            "jersey": p.get("jersey", ""),
            "pos": (p.get("position") or {}).get("abbreviation", ""),
            "headshot": (athlete.get("headshot") or {}).get("href") if isinstance(athlete.get("headshot"), dict) else None,
        }
        if p.get("starter"):
            starters.append(entry)
        else:
            subs.append(entry)
    return {"team": team_name, "formation": formation, "starters": starters, "subs": subs}


def fetch(window_hours: int = 24, force: bool = False) -> None:
    if not PRED.exists():
        print("[lineups] no predictions.json"); return

    data = json.load(open(PRED))
    now = datetime.now(timezone.utc)
    cutoff_start = now - timedelta(hours=2)   # already started (may have lineup)
    cutoff_end   = now + timedelta(hours=window_hours)

    # Upcoming + recently started fixtures
    candidates = [
        f for f in data.get("fixtures", [])
        if not f.get("completed")
        and cutoff_start <= datetime.fromisoformat(f["commence_time"].replace("Z", "+00:00")) <= cutoff_end
    ]
    # Also include matches that started in last 2h (lineup already published)
    live = [
        f for f in data.get("fixtures", [])
        if not f.get("completed")
        and datetime.fromisoformat(f["commence_time"].replace("Z", "+00:00")) >= cutoff_start - timedelta(hours=2)
        and datetime.fromisoformat(f["commence_time"].replace("Z", "+00:00")) < now
    ]
    candidates = list({f["id"]: f for f in candidates + live}.values())

    existing = json.load(open(OUT)) if OUT.exists() else {}

    needed_dates = set()
    for fx in candidates:
        fid = fx["id"]
        if force or fid not in existing:
            needed_dates.add(fx["commence_time"][:10])

    if not needed_dates and not force:
        print(f"[lineups] {len(candidates)} candidates, all cached")
        return

    scoreboard = fetch_scoreboard_dates(needed_dates)
    known_players = _load_known_players()

    n_fetched = 0
    for fx in candidates:
        fid = fx["id"]
        if not force and fid in existing:
            continue
        home_n = _norm_alias(fx["home"])
        away_n = _norm_alias(fx["away"])
        date   = fx["commence_time"][:10]
        espn_id = scoreboard.get((home_n, away_n, date))
        if not espn_id:
            for (h, a, d), eid in scoreboard.items():
                if h == home_n and a == away_n:
                    espn_id = eid; break
        if not espn_id:
            print(f"[lineups] no ESPN id for {fx['home']} vs {fx['away']}")
            continue

        try:
            r = requests.get(ESPN_SUM, params={"event": espn_id}, timeout=15)
            r.raise_for_status()
            summary = r.json()
        except Exception as e:
            print(f"[lineups] summary failed {espn_id}: {e}"); continue

        rosters = summary.get("rosters", [])
        if not rosters:
            print(f"[lineups] no rosters yet for {fx['home']} vs {fx['away']}")
            continue

        teams = [_extract_roster(t) for t in rosters]
        has_starters = any(len(t["starters"]) > 0 for t in teams)
        if not has_starters:
            print(f"[lineups] rosters empty (not published yet) for {fx['home']} vs {fx['away']}")
            continue

        # Detect notable players not in starting XI
        # Uses fuzzy last-name matching to handle "Marcus Pedersen" vs "Marcus Holmgren Pedersen"
        def _last(name):
            return _norm(name.split()[-1]) if name else ""

        absences = {}
        for t in teams:
            team_known = known_players.get(t["team"], set())
            starter_lasts = {_last(s["name"]) for s in t["starters"]}
            starter_fulls = {s["name"] for s in t["starters"]}
            not_starting = []
            for p in team_known:
                if p not in starter_fulls and _last(p) not in starter_lasts:
                    not_starting.append(p)
            if not_starting:
                absences[t["team"]] = not_starting

        existing[fid] = {
            "espn_id": espn_id,
            "fetched_at": now.isoformat(timespec="seconds"),
            "teams": teams,
            "absences": absences,
        }
        summary_str = ", ".join(f"{t['team']} {t['formation']} ({len(t['starters'])} starters)" for t in teams)
        print(f"[lineups] {fx['home']} vs {fx['away']}: {summary_str}")
        if absences:
            for team, players in absences.items():
                print(f"  ⚠ {team} absent: {', '.join(players)}")
        n_fetched += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(existing, open(OUT, "w"), indent=2, ensure_ascii=False)
    print(f"[lineups] {n_fetched} fetched · total cached: {len(existing)}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=24, help="Hours ahead to fetch")
    ap.add_argument("--force", action="store_true", help="Re-fetch all")
    args = ap.parse_args()
    fetch(window_hours=args.window, force=args.force)
