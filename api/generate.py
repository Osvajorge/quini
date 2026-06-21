"""Fetch live odds + scores from The Odds API, run Dixon-Coles predictions, output JSON."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.config import THE_ODDS_API_KEY_FREE, require
from model.dixon_coles import load_fit
from model.markets import all_markets, score_matrix
from model.predict import predict, EDGE_THRESHOLD
from model.markets import score_matrix as compute_score_matrix
from quiniela.tournament import risk_adjusted_pick

BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_fifa_world_cup"
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "predictions.json"

TEAM_NAME_MAP = {
    "USA": "United States",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Ivory Coast": "Côte d'Ivoire",
    "Cote D'Ivoire": "Côte d'Ivoire",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
}


def normalize_team(api_name: str) -> str:
    return TEAM_NAME_MAP.get(api_name, api_name)


def fetch_odds(api_key: str) -> list[dict]:
    r = requests.get(
        f"{BASE}/sports/{SPORT}/odds",
        params={
            "apiKey": api_key,
            "regions": "eu",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        },
        timeout=20,
    )
    r.raise_for_status()
    remaining = r.headers.get("x-requests-remaining", "?")
    print(f"odds: {len(r.json())} events · credits remaining: {remaining}")
    return r.json()


def fetch_scores(api_key: str, days_from: int = 3) -> list[dict]:
    r = requests.get(
        f"{BASE}/sports/{SPORT}/scores",
        params={"apiKey": api_key, "daysFrom": days_from},
        timeout=20,
    )
    r.raise_for_status()
    remaining = r.headers.get("x-requests-remaining", "?")
    print(f"scores: {len(r.json())} events · credits remaining: {remaining}")
    return r.json()


def extract_odds_from_event(event: dict) -> dict:
    odds = {}
    for bm in event.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt["key"] == "h2h":
                for o in mkt["outcomes"]:
                    if o["name"] == event["home_team"]:
                        odds["home"] = o["price"]
                    elif o["name"] == event["away_team"]:
                        odds["away"] = o["price"]
                    else:
                        odds["draw"] = o["price"]
            elif mkt["key"] == "totals":
                for o in mkt["outcomes"]:
                    point = o.get("point", 2.5)
                    if point == 2.5:
                        if o["name"] == "Over":
                            odds["over_2_5"] = o["price"]
                        else:
                            odds["under_2_5"] = o["price"]
        if "home" in odds:
            break
    return odds


def build_score_predictions(fit, home_model: str, away_model: str) -> tuple[list[dict], dict | None]:
    try:
        lam_h, lam_a = fit.expected_goals(home_model, away_model, neutral=False)
    except KeyError:
        return [], None
    matrix = compute_score_matrix(lam_h, lam_a, fit.rho)
    top_scores = []
    n = matrix.shape[0]
    flat = []
    for h in range(min(n, 6)):
        for a in range(min(n, 6)):
            flat.append((h, a, float(matrix[h, a])))
    flat.sort(key=lambda x: -x[2])
    for h, a, p in flat[:5]:
        top_scores.append({"home": h, "away": a, "prob": round(p * 100, 1)})

    ph, pa, metrics = risk_adjusted_pick(matrix)
    quiniela = {
        "pick_home": ph,
        "pick_away": pa,
        "e_pts": round(metrics["e_pts"], 2),
        "sd": round(metrics["sd"], 2),
        "e_tie": round(metrics["e_tie"], 2),
        "objective": round(metrics["objective"], 2),
        "alternatives": [
            {"home": alt["ph"], "away": alt["pa"], "obj": round(alt["objective"], 2)}
            for alt in metrics["alternatives"][:3]
        ],
    }
    return top_scores, quiniela


def generate():
    api_key = require("THE_ODDS_API_KEY_FREE", THE_ODDS_API_KEY_FREE)
    fit = load_fit()

    odds_data = fetch_odds(api_key)
    scores_data = fetch_scores(api_key, days_from=3)

    score_map = {}
    for ev in scores_data:
        score_map[ev["id"]] = ev

    seen_ids = set()
    fixtures = []

    for event in odds_data:
        event_id = event["id"]
        seen_ids.add(event_id)
        home_api = event["home_team"]
        away_api = event["away_team"]
        home_model = normalize_team(home_api)
        away_model = normalize_team(away_api)
        commence = event["commence_time"]

        odds = extract_odds_from_event(event)
        if not odds.get("home"):
            continue

        score_info = score_map.get(event_id, {})
        completed = score_info.get("completed", False)
        scores = score_info.get("scores")
        actual_home = None
        actual_away = None
        is_live = False
        if scores:
            for s in scores:
                if s["name"] == home_api:
                    actual_home = int(s["score"]) if s["score"] is not None else None
                elif s["name"] == away_api:
                    actual_away = int(s["score"]) if s["score"] is not None else None
            if not completed and actual_home is not None:
                is_live = True

        try:
            pred = predict(fit, home_model, away_model, odds, neutral=False)
        except KeyError:
            continue

        bets = [p for p in pred["picks"] if p["pick"] == "BET"]
        best_bet = max(bets, key=lambda p: p["edge"]) if bets else None

        top_scores, quiniela = build_score_predictions(fit, home_model, away_model)

        fixture = {
            "id": event_id,
            "home": home_api,
            "away": away_api,
            "commence_time": commence,
            "completed": completed,
            "is_live": is_live,
            "actual_home": actual_home,
            "actual_away": actual_away,
            "xg_home": round(pred["xg_home"], 2),
            "xg_away": round(pred["xg_away"], 2),
            "odds": {k: round(v, 2) for k, v in odds.items()},
            "picks": [],
            "best_bet": None,
            "top_scores": top_scores,
            "quiniela": quiniela,
            "fixture_confidence": pred["fixture_confidence"],
        }

        for p in pred["picks"]:
            fixture["picks"].append({
                "market": p["market"],
                "side": p["side"],
                "model_prob": round(p["model_prob"] * 100, 1),
                "devig_prob": round(p["devig_prob"] * 100, 1),
                "edge": round(p["edge"] * 100, 1),
                "odds": round(p["odds"], 2),
                "pick": p["pick"],
                "confidence": p["confidence_band"],
            })

        if best_bet:
            fixture["best_bet"] = {
                "market": best_bet["market"],
                "edge": round(best_bet["edge"] * 100, 1),
                "odds": round(best_bet["odds"], 2),
                "pick": best_bet["pick"],
                "confidence": best_bet["confidence_band"],
            }

        fixtures.append(fixture)

    for ev in scores_data:
        if ev["id"] in seen_ids:
            continue
        if not ev.get("completed"):
            continue
        home_api = ev["home_team"]
        away_api = ev["away_team"]
        actual_home = None
        actual_away = None
        if ev.get("scores"):
            for s in ev["scores"]:
                if s["name"] == home_api:
                    actual_home = int(s["score"]) if s["score"] is not None else None
                elif s["name"] == away_api:
                    actual_away = int(s["score"]) if s["score"] is not None else None
        home_model = normalize_team(home_api)
        away_model = normalize_team(away_api)
        top_scores, quiniela = build_score_predictions(fit, home_model, away_model)
        fixtures.append({
            "id": ev["id"],
            "home": home_api,
            "away": away_api,
            "commence_time": ev["commence_time"],
            "completed": True,
            "is_live": False,
            "actual_home": actual_home,
            "actual_away": actual_away,
            "xg_home": None,
            "xg_away": None,
            "odds": {},
            "picks": [],
            "best_bet": None,
            "top_scores": top_scores,
            "quiniela": quiniela,
            "fixture_confidence": None,
        })

    now = datetime.now(timezone.utc)
    completed_fixtures = [f for f in fixtures if f["completed"]]
    upcoming_fixtures = [f for f in fixtures if not f["completed"]]
    upcoming_fixtures.sort(key=lambda f: f["commence_time"])

    bet_picks = [f for f in fixtures if f["best_bet"]]
    total_bets = len(bet_picks)
    won = sum(1 for f in completed_fixtures if f["best_bet"] and _bet_won(f))
    lost = sum(1 for f in completed_fixtures if f["best_bet"] and not _bet_won(f) and f["actual_home"] is not None)

    output = {
        "generated_at": now.isoformat(),
        "model": {
            "name": "Dixon-Coles",
            "teams": len(fit.teams),
            "matches_trained": fit.n_matches,
            "fitted_at": fit.fitted_at.isoformat(),
            "edge_threshold": EDGE_THRESHOLD * 100,
        },
        "stats": {
            "total_fixtures": len(fixtures),
            "upcoming": len(upcoming_fixtures),
            "completed": len(completed_fixtures),
            "total_bets": total_bets,
            "won": won,
            "lost": lost,
            "win_rate": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0,
        },
        "fixtures": fixtures,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)
    print(f"output: {OUT} ({len(fixtures)} fixtures)")


def _bet_won(fixture: dict) -> bool:
    if not fixture.get("best_bet") or fixture["actual_home"] is None:
        return False
    bb = fixture["best_bet"]
    side = bb.get("market", "")
    ah = fixture["actual_home"]
    aa = fixture["actual_away"]
    if "Local" in side or side == "home":
        return ah > aa
    if "Visitante" in side or side == "away":
        return aa > ah
    if "Empate" in side or side == "draw":
        return ah == aa
    if "Over" in side:
        return (ah + aa) > 2.5
    if "Under" in side:
        return (ah + aa) < 2.5
    if "BTTS" in side and "Sí" in side:
        return ah > 0 and aa > 0
    if "BTTS" in side and "No" in side:
        return ah == 0 or aa == 0
    return False


if __name__ == "__main__":
    generate()
