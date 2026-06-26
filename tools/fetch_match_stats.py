"""Fetch ESPN match boxscore + leaders per completed fixture.

Persists to docs/data/match_stats.json keyed by Quini fixture_id (not ESPN
id, which we cross-reference via team name + commence_time match).

Provides per-team:
- shots, shotsOnGoal, possession, fouls, corners, offsides, cards
- top scorer / top assist / top stat leaders

Used by:
- Frontend: enriches match summary cards with shots/cards/possession
- Future: per-player aggregation for player profile cards

Run: python -m tools.fetch_match_stats
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PRED = ROOT / "docs" / "data" / "predictions.json"
OUT = ROOT / "docs" / "data" / "match_stats.json"

ESPN_SB = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_SUM = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"

# Stats we care about per team (mapped from ESPN names)
TEAM_STAT_KEYS = {
    "shotsTotal", "shotsOnGoal", "possessionPct", "totalGoals",
    "foulsCommitted", "yellowCards", "redCards", "offsides", "wonCorners",
    "totalPasses", "passingAccuracy", "totalSaves", "totalTackles",
    "totalLongBalls", "totalCrosses", "interceptions",
}


_ESPN_ALIASES: dict[str, str] = {
    # ESPN name → Odds API name (both will be _norm'd)
    "unitedstates": "usa",
    "unitedstatesmennationalteam": "usa",
    "usmensnationalteam": "usa",
    "korearepublic": "southkorea",
    "republicofireland": "ireland",
    "northernireland": "northernireland",
    "democraticrepublicofcongo": "drcongo",
    "drcongo": "drcongo",
    "capeverde": "capeverde",
    "costarica": "costarica",
    "saudiarabia": "saudiarabia",
    "newzealand": "newzealand",
}


def _norm(name: str) -> str:
    n = unicodedata.normalize("NFD", (name or "").lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.replace(" ", "").replace(".", "").replace("'", "").replace("-", "")
    return _ESPN_ALIASES.get(n, n)


def _load_existing() -> dict:
    if OUT.exists():
        try:
            return json.load(open(OUT))
        except Exception:
            return {}
    return {}


def fetch_scoreboard_for_dates(dates: set[str]) -> dict:
    """Query ESPN scoreboard per date (YYYYMMDD). Returns
    {(home_norm, away_norm, date_iso): espn_event_id}.
    """
    mapping = {}
    for d in dates:
        try:
            r = requests.get(ESPN_SB, params={"dates": d.replace("-", "")}, timeout=15)
            r.raise_for_status()
            events = r.json().get("events", [])
        except Exception as e:
            print(f"[stats] scoreboard fetch failed for {d}: {e}")
            continue
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            home = away = None
            for c in competitors:
                team = (c.get("team") or {}).get("displayName", "")
                if c.get("homeAway") == "home":
                    home = team
                elif c.get("homeAway") == "away":
                    away = team
            if home and away:
                date = (ev.get("date") or "")[:10]
                mapping[(_norm(home), _norm(away), date)] = ev["id"]
    return mapping


def fetch_summary(event_id: str) -> dict | None:
    try:
        r = requests.get(ESPN_SUM, params={"event": event_id}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[stats] summary fetch failed for {event_id}: {e}")
        return None


def _extract_team_stats(team_block: dict) -> dict:
    """ESPN team boxscore → dict of all stats (raw + normalized)."""
    out = {"team": (team_block.get("team") or {}).get("displayName", ""),
           "side": team_block.get("homeAway", ""),
           "stats": {}}
    for s in team_block.get("statistics", []):
        name = s.get("name")
        if not name:
            continue
        v = s.get("displayValue") or s.get("value")
        out["stats"][name] = v
    return out


def _extract_leaders(summary: dict) -> list[dict]:
    """Top performers per stat category (shots, passes, saves — NOT goals/assists)."""
    leaders = []
    skip_cats = {"Goals", "Assists"}
    for L in summary.get("leaders", []) or []:
        for cat in L.get("leaders", []) or []:
            if cat.get("displayName") in skip_cats:
                continue
            for athlete in (cat.get("leaders") or [])[:1]:
                a = athlete.get("athlete", {})
                leaders.append({
                    "team": (L.get("team") or {}).get("displayName", ""),
                    "category": cat.get("displayName"),
                    "player": a.get("displayName"),
                    "headshot": a.get("headshot", {}).get("href") if isinstance(a.get("headshot"), dict) else None,
                    "value": athlete.get("displayValue"),
                })
    return leaders


def _extract_goals_assists(summary: dict) -> list[dict]:
    """Parse ALL goals and assists from keyEvents (complete scorer data)."""
    import re
    entries = []
    for ev in summary.get("keyEvents", []):
        ev_type = ev.get("type", {}).get("type", "")  # slug: "goal", "goal---volley", "own-goal"
        if "goal" not in ev_type or "own-goal" in ev_type:
            continue
        text = ev.get("text", "")
        team_name = ev.get("team", {}).get("displayName", "")
        clock = ev.get("clock", {}).get("displayValue", "")
        # Text format: "Goal! Portugal 2, Uzbekistan 0. Nuno Mendes (Portugal) ..."
        m = re.search(r"(?:Goal[^.]*\.\s+)(.+?)\s+\(", text)
        if not m:
            m = re.search(r"\d+\.\s*(.+?)\s*\(", text)
        if m:
            entries.append({
                "team": team_name,
                "category": "Goals",
                "player": m.group(1).strip(),
                "headshot": None,
                "value": "1",
                "clock": clock,
            })
        am = re.search(r"Assisted by (.+?)(?:\s+with|\s*\.)", text)
        if am:
            entries.append({
                "team": team_name,
                "category": "Assists",
                "player": am.group(1).strip(),
                "headshot": None,
                "value": "1",
                "clock": clock,
            })
    return entries


def fetch(refresh: bool = False) -> None:
    if not PRED.exists():
        print("[stats] no predictions.json; skipping")
        return

    data = json.load(open(PRED))
    fixtures = data.get("fixtures", [])
    completed = [f for f in fixtures if f.get("completed")]
    print(f"[stats] {len(completed)} completed fixtures to check")

    existing = _load_existing()

    def _needs_fetch(fid):
        if fid not in existing or not existing[fid].get("teams"):
            return True
        return refresh

    needed_dates = {fx.get("commence_time", "")[:10] for fx in completed
                    if _needs_fetch(fx.get("id"))}
    needed_dates = {d for d in needed_dates if d}
    print(f"[stats] querying ESPN for {len(needed_dates)} dates")
    scoreboard = fetch_scoreboard_for_dates(needed_dates)

    n_fetched = 0
    for fx in completed:
        fid = fx["id"]
        if not _needs_fetch(fid):
            continue

        home_n = _norm(fx["home"])
        away_n = _norm(fx["away"])
        date = fx.get("commence_time", "")[:10]
        espn_id = scoreboard.get((home_n, away_n, date))
        if not espn_id:
            # Try date-flexible match (±1 day)
            for (h, a, d), eid in scoreboard.items():
                if h == home_n and a == away_n:
                    espn_id = eid
                    break
        if not espn_id:
            continue

        summary = fetch_summary(espn_id)
        if not summary:
            continue

        teams_block = summary.get("boxscore", {}).get("teams", [])
        teams_data = [_extract_team_stats(t) for t in teams_block]
        leaders = _extract_leaders(summary)
        leaders += _extract_goals_assists(summary)

        existing[fid] = {
            "espn_id": espn_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "teams": teams_data,
            "leaders": leaders,
        }
        n_fetched += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(existing, open(OUT, "w"), indent=2, ensure_ascii=False)
    print(f"[stats] {n_fetched} new fixtures fetched · total cached: {len(existing)}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="Re-fetch to add goals/assists data")
    args = ap.parse_args()
    fetch(refresh=args.refresh)
