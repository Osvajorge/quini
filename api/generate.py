"""Fetch live odds + scores from The Odds API, run Dixon-Coles predictions, output JSON."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.config import THE_ODDS_API_KEY_FREE, require
from model.bivariate_poisson import load_fit
from model.markets import score_matrix as compute_score_matrix
from model.predict import predict, EDGE_THRESHOLD
from quiniela.tournament import risk_adjusted_pick

BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_fifa_world_cup"
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "predictions.json"
HISTORY_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "history.json"

TEAM_NAME_MAP = {
    "USA": "United States",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Cote D'Ivoire": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
}

PICK_DESC = {
    "Under 2.5": {"es": "Menos de 3 goles en el partido", "en": "Less than 3 total goals in the match"},
    "Over 2.5": {"es": "3 o más goles en el partido", "en": "3 or more total goals in the match"},
    "Gana Local (1)": {"es": "{home} gana el partido", "en": "{home} wins the match"},
    "Gana Visitante (2)": {"es": "{away} gana el partido", "en": "{away} wins the match"},
    "Empate (X)": {"es": "El partido termina en empate", "en": "Match ends in a draw"},
    "BTTS Sí": {"es": "Ambos equipos anotan al menos un gol", "en": "Both teams score at least one goal"},
    "BTTS No": {"es": "Al menos un equipo se queda sin anotar", "en": "At least one team fails to score"},
}

MARKET_GROUPS = {
    "home": "1x2", "draw": "1x2", "away": "1x2",
    "over_2_5": "ou", "under_2_5": "ou",
    "btts_yes": "btts", "btts_no": "btts",
}

# Compound bet descriptions: (primary_side, secondary_side) → (es, en)
COMPOUND_DESC: dict[tuple[str, str], tuple[str, str]] = {
    ("home",      "over_2_5"):  ("{home} gana + Más de 2.5 goles",           "{home} wins + Over 2.5 goals"),
    ("home",      "under_2_5"): ("{home} gana + Menos de 2.5 goles",         "{home} wins + Under 2.5 goals"),
    ("home",      "btts_yes"):  ("{home} gana y ambos anotan",                "{home} wins + Both teams score"),
    ("home",      "btts_no"):   ("{home} gana dejando al rival a cero",       "{home} wins to nil"),
    ("away",      "over_2_5"):  ("{away} gana + Más de 2.5 goles",           "{away} wins + Over 2.5 goals"),
    ("away",      "under_2_5"): ("{away} gana + Menos de 2.5 goles",         "{away} wins + Under 2.5 goals"),
    ("away",      "btts_yes"):  ("{away} gana y ambos anotan",                "{away} wins + Both teams score"),
    ("away",      "btts_no"):   ("{away} gana dejando al rival a cero",       "{away} wins to nil"),
    ("draw",      "under_2_5"): ("Empate con menos de 3 goles en total",     "Draw + Under 2.5 goals"),
    ("draw",      "btts_no"):   ("Empate sin marcar ambos equipos",           "Draw + At least one team scoreless"),
    ("over_2_5",  "btts_yes"):  ("Más de 2.5 goles y ambos equipos anotan",  "Over 2.5 + Both teams score"),
    ("under_2_5", "btts_no"):   ("Menos de 3 goles y al menos uno a cero",   "Under 2.5 + At least one scoreless"),
}

COMPATIBLE_GROUPS = {"1x2": {"ou", "btts"}, "ou": {"btts", "1x2"}, "btts": {"ou"}}

TOOLTIPS = {
    "xg": {"es": "Goles esperados en el partido", "en": "Expected goals in the match"},
    "edge": {"es": "Ventaja sobre las casas de apuestas. Positivo = buena oportunidad", "en": "Advantage over bookmakers. Positive = good opportunity"},
    "confidence": {"es": "Qué tan segura es esta predicción", "en": "How confident this prediction is"},
    "model_prob": {"es": "Probabilidad según modelo", "en": "Model probability"},
    "devig_prob": {"es": "Probabilidad según mercado", "en": "Market probability"},
    "odds": {"es": "Cuota que pagan las casas de apuestas", "en": "Bookmaker odds"},
    "best_bet": {"es": "Apuesta con mayor ventaja en este partido", "en": "Bet with the biggest advantage in this match"},
    "e_pts": {"es": "Puntos esperados: +3 acertar ganador, +1 por goles exactos", "en": "Expected points: +3 correct winner, +1 per exact goals"},
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
            elif mkt["key"] == "btts":
                for o in mkt["outcomes"]:
                    if o["name"] == "Yes":
                        odds["btts_yes"] = o["price"]
                    else:
                        odds["btts_no"] = o["price"]
        if "home" in odds:
            break
    return odds


def build_score_predictions(fit, home_model: str, away_model: str) -> tuple[list[dict], dict | None, float, float]:
    try:
        grid = fit.predict_grid(home_model, away_model, neutral=False)
        lam_h, lam_a = fit.expected_goals(home_model, away_model, neutral=False)
    except KeyError:
        return [], None, 0.0, 0.0
    flat = [(h, a, grid.exact_score(h, a)) for h in range(6) for a in range(6)]
    flat.sort(key=lambda x: -x[2])
    top_scores = [{"home": h, "away": a, "prob": round(p * 100, 1)} for h, a, p in flat[:5]]
    matrix = fit.score_matrix(home_model, away_model, neutral=False, n=10)

    strategies = {}
    for label, lv, lt in [("balanced", 0.6, 0.4), ("aggressive", 0.9, 0.4), ("defensive", 0.0, 0.3)]:
        ph, pa, metrics = risk_adjusted_pick(matrix, lambda_var=lv, lambda_tie=lt)
        strategies[label] = {
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
    quiniela = strategies
    return top_scores, quiniela, lam_h, lam_a


def build_live_scores(fit, home_model: str, away_model: str, lam_h: float, lam_a: float,
                       current_h: int, current_a: int, commence: str) -> list[dict]:
    """Compute conditional score probabilities for a live fixture."""
    try:
        now = datetime.now(timezone.utc)
        commence_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        elapsed = (now - commence_dt).total_seconds() / 60.0
        elapsed = max(0.0, min(90.0, elapsed))
        r = max(0.05, (90.0 - elapsed) / 90.0)

        lam_h_rem = lam_h * r
        lam_a_rem = lam_a * r

        remaining_matrix = compute_score_matrix(lam_h_rem, lam_a_rem, 0.0)
        n = remaining_matrix.shape[0]

        flat = []
        for rh in range(min(n, 6)):
            for ra in range(min(n, 6)):
                final_h = current_h + rh
                final_a = current_a + ra
                flat.append((final_h, final_a, float(remaining_matrix[rh, ra])))

        # Aggregate by (final_h, final_a) in case of duplicates
        score_agg: dict[tuple[int, int], float] = {}
        for fh, fa, p in flat:
            key = (fh, fa)
            score_agg[key] = score_agg.get(key, 0.0) + p

        flat_agg = [(fh, fa, p) for (fh, fa), p in score_agg.items()]
        flat_agg.sort(key=lambda x: -x[2])

        live_scores = []
        for fh, fa, p in flat_agg[:5]:
            live_scores.append({"home": fh, "away": fa, "prob": round(p * 100, 1)})
        return live_scores
    except Exception:
        return []


def _pick_description(market: str, home: str, away: str) -> tuple[str, str]:
    desc = PICK_DESC.get(market)
    if not desc:
        return market, market
    es = desc["es"].replace("{home}", home).replace("{away}", away)
    en = desc["en"].replace("{home}", home).replace("{away}", away)
    return es, en


def _compound_description(best_side: str, bets: list[dict], home: str, away: str) -> tuple[str, str] | None:
    """Build compound description if a second compatible BET exists."""
    best_group = MARKET_GROUPS.get(best_side)
    if not best_group:
        return None
    ok_groups = COMPATIBLE_GROUPS.get(best_group, set())
    secondary = max(
        (b for b in bets if b["side"] != best_side and MARKET_GROUPS.get(b["side"]) in ok_groups),
        key=lambda b: b["edge"],
        default=None,
    )
    if secondary is None:
        return None
    tmpl = COMPOUND_DESC.get((best_side, secondary["side"]))
    if tmpl is None:
        return None
    return (
        tmpl[0].replace("{home}", home).replace("{away}", away),
        tmpl[1].replace("{home}", home).replace("{away}", away),
    )


def _load_history() -> dict:
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"fixtures": [], "summary": {}}


def _save_history(history: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


def _update_history_summary(history: dict) -> None:
    fixtures = history.get("fixtures", [])
    total = len(fixtures)
    won = sum(1 for fx in fixtures if fx.get("best_bet_won") is True)
    lost = sum(1 for fx in fixtures if fx.get("best_bet_won") is False)
    # ROI: won → profit = (odds-1), lost → profit = -1
    total_profit = 0.0
    for fx in fixtures:
        bb = fx.get("best_bet")
        if bb and fx.get("best_bet_won") is not None:
            if fx["best_bet_won"]:
                total_profit += (bb.get("odds", 1.0) - 1.0)
            else:
                total_profit -= 1.0
    roi = round(total_profit / total * 100, 1) if total > 0 else 0.0
    history["summary"] = {
        "total": total,
        "won": won,
        "lost": lost,
        "win_rate": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0.0,
        "roi": roi,
    }


def _accumulate_history(old_predictions: dict, new_score_map: dict) -> None:
    """Find newly completed fixtures and save their pre-match picks to history."""
    history = _load_history()
    existing_ids = {fx["id"] for fx in history.get("fixtures", [])}

    old_fixtures = {fx["id"]: fx for fx in old_predictions.get("fixtures", [])}

    for fx_id, old_fx in old_fixtures.items():
        if old_fx.get("completed"):
            continue  # was already completed before, skip
        new_score = new_score_map.get(fx_id)
        if not new_score:
            continue
        if not new_score.get("completed"):
            continue
        if fx_id in existing_ids:
            continue  # already recorded in history

        # Extract actual result from new scores
        actual_home = None
        actual_away = None
        home_api = old_fx.get("home", "")
        away_api = old_fx.get("away", "")
        if new_score.get("scores"):
            for s in new_score["scores"]:
                if s["name"] == home_api:
                    actual_home = int(s["score"]) if s["score"] is not None else None
                elif s["name"] == away_api:
                    actual_away = int(s["score"]) if s["score"] is not None else None

        # Build picks list (BET signals only) from old predictions
        picks = [
            {
                "market": p["market"],
                "pick": p["pick"],
                "edge": p["edge"],
                "odds": p["odds"],
                "model_prob": p["model_prob"],
                "devig_prob": p["devig_prob"],
            }
            for p in old_fx.get("picks", [])
            if p.get("pick") == "BET"
        ]

        best_bet = old_fx.get("best_bet")
        if not best_bet:
            continue  # no bet signal → nothing to track

        best_bet_won = None
        if best_bet and actual_home is not None and actual_away is not None:
            temp_fx = {
                "best_bet": best_bet,
                "actual_home": actual_home,
                "actual_away": actual_away,
            }
            best_bet_won = _bet_won(temp_fx)

        history_entry = {
            "id": fx_id,
            "home": home_api,
            "away": away_api,
            "date": old_fx.get("commence_time"),
            "picks": picks,
            "best_bet": best_bet,
            "result": {"home": actual_home, "away": actual_away},
            "best_bet_won": best_bet_won,
        }
        history["fixtures"].append(history_entry)

    _update_history_summary(history)
    _save_history(history)


def generate():
    api_key = require("THE_ODDS_API_KEY_FREE", THE_ODDS_API_KEY_FREE)

    # --- Smart cron exit: load existing predictions to check match windows ---
    existing_predictions: dict = {}
    if OUT.exists():
        try:
            with open(OUT) as f:
                existing_predictions = json.load(f)
        except Exception:
            existing_predictions = {}

    now = datetime.now(timezone.utc)

    if existing_predictions:
        has_live = any(fx.get("is_live") for fx in existing_predictions.get("fixtures", []))

        if has_live:
            # Live match active — always run (cron is now */5, no extra rate limit)
            print("live match detected — running update")
        else:
            # Check 2-hour match window
            non_completed = [
                fx for fx in existing_predictions.get("fixtures", [])
                if not fx.get("completed")
            ]
            should_run = False
            for fx in non_completed:
                try:
                    ct = datetime.fromisoformat(fx["commence_time"].replace("Z", "+00:00"))
                    minutes_until = (ct - now).total_seconds() / 60.0
                    if minutes_until <= 120:
                        should_run = True
                        break
                except Exception:
                    should_run = True
                    break
            if not should_run:
                print("no matches in window, skipping")
                return
            # Rate limit non-live runs to 15 min even when cron is */5
            last_gen = existing_predictions.get("generated_at")
            if last_gen:
                try:
                    last_dt = datetime.fromisoformat(last_gen.replace("Z", "+00:00"))
                    mins_since = (now - last_dt).total_seconds() / 60.0
                    if mins_since < 15:
                        print(f"rate limited (no live): {mins_since:.1f}m since last run, need 15m")
                        return
                except Exception:
                    pass

    fit = load_fit()

    odds_data = fetch_odds(api_key)
    scores_data = fetch_scores(api_key)

    score_map = {}
    for ev in scores_data:
        score_map[ev["id"]] = ev

    # --- History accumulation: find newly completed fixtures ---
    if existing_predictions:
        _accumulate_history(existing_predictions, score_map)

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

        top_scores, quiniela, lam_h, lam_a = build_score_predictions(fit, home_model, away_model)

        # --- Live conditional score probabilities ---
        live_scores = None
        if is_live and actual_home is not None and actual_away is not None:
            live_scores = build_live_scores(
                fit, home_model, away_model, lam_h, lam_a,
                actual_home, actual_away, commence
            )

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
            "live_scores": live_scores,
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
            compound = _compound_description(best_bet["side"], bets, home_api, away_api)
            if compound:
                desc_es, desc_en = compound
            else:
                desc_es, desc_en = _pick_description(best_bet["market"], home_api, away_api)
            fixture["best_bet"] = {
                "market": best_bet["market"],
                "edge": round(best_bet["edge"] * 100, 1),
                "odds": round(best_bet["odds"], 2),
                "pick": best_bet["pick"],
                "confidence": best_bet["confidence_band"],
                "description_es": desc_es,
                "description_en": desc_en,
                "is_compound": compound is not None,
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
        top_scores, quiniela, _lh, _la = build_score_predictions(fit, home_model, away_model)
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
            "live_scores": None,
            "quiniela": quiniela,
            "fixture_confidence": None,
        })

    current_ids = {f["id"] for f in fixtures}
    if existing_predictions:
        for old_fx in existing_predictions.get("fixtures", []):
            if old_fx.get("completed") and old_fx["id"] not in current_ids:
                fixtures.append(old_fx)

    completed_keys = {
        (f["home"], f["away"], f["commence_time"])
        for f in fixtures if f["completed"]
    }
    fixtures = [
        f for f in fixtures
        if f["completed"] or (f["home"], f["away"], f["commence_time"]) not in completed_keys
    ]

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
            "name": "Bivariate-Poisson",
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
        "tooltips": TOOLTIPS,
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
