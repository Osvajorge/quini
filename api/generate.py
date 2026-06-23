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


def fetch_standings() -> list[dict]:
    """Fetch group standings from ESPN. Returns list of {group, teams[]}."""
    try:
        r = requests.get(
            "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[standings] ESPN fetch failed: {e}")
        return []

    STAT = {"gamesPlayed": "played", "wins": "won", "ties": "drawn", "losses": "lost",
            "pointsFor": "gf", "pointsAgainst": "ga", "pointDifferential": "gd", "points": "pts"}
    groups = []
    for grp in data.get("children", []):
        teams = []
        for entry in grp.get("standings", {}).get("entries", []):
            team = entry.get("team", {})
            stats_raw = {s["name"]: s["displayValue"] for s in entry.get("stats", [])}
            def _int(k): return int(stats_raw.get(k, 0) or 0)
            logos = team.get("logos", [])
            flag = logos[0]["href"] if logos else ""
            note = entry.get("note", {})
            teams.append({
                "name": team.get("displayName", ""),
                "abbr": team.get("abbreviation", ""),
                "flag": flag,
                "rank": _int("rank"),
                "played": _int("gamesPlayed"),
                "won": _int("wins"),
                "drawn": _int("ties"),
                "lost": _int("losses"),
                "gf": _int("pointsFor"),
                "ga": _int("pointsAgainst"),
                "gd": stats_raw.get("pointDifferential", "0"),
                "pts": _int("points"),
                "status": "direct" if "81D6AC" in note.get("color","") else "possible" if "B5E7CE" in note.get("color","") else "eliminated",
            })
        teams.sort(key=lambda t: (t["rank"] or 99))
        groups.append({"group": grp.get("name", ""), "teams": teams})

    print(f"[standings] fetched {len(groups)} groups")
    return groups


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


_BOOK_DISPLAY: dict[str, str] = {
    "pinnacle": "Pinnacle", "bet365": "bet365", "unibet_eu": "Unibet",
    "betfair_ex_eu": "Betfair", "onexbet": "1xBet", "sport888": "888sport",
    "bwin": "Bwin", "marathonbet": "Marathon", "betclic_eu": "Betclic",
    "william_hill": "William Hill", "betway": "Betway", "nordicbet": "Nordic",
    "casumo": "Casumo", "coolbet": "Coolbet", "suprabets": "Supra",
}
# Priority order for de-vig: sharp books first
_SHARP_BOOKS = ["pinnacle", "betfair_ex_eu", "marathonbet", "bwin"]


def _parse_book_odds(bm: dict, home_name: str, away_name: str) -> dict:
    """Extract h2h, totals and btts from one bookmaker dict."""
    out = {}
    for mkt in bm.get("markets", []):
        if mkt["key"] == "h2h":
            for o in mkt["outcomes"]:
                if o["name"] == home_name:
                    out["home"] = o["price"]
                elif o["name"] == away_name:
                    out["away"] = o["price"]
                else:
                    out["draw"] = o["price"]
        elif mkt["key"] == "totals":
            for o in mkt["outcomes"]:
                if o.get("point", 2.5) == 2.5:
                    out["over_2_5" if o["name"] == "Over" else "under_2_5"] = o["price"]
        elif mkt["key"] == "btts":
            for o in mkt["outcomes"]:
                out["btts_yes" if o["name"] == "Yes" else "btts_no"] = o["price"]
    return out


def extract_odds_from_event(event: dict) -> tuple[dict, dict, list[dict]]:
    """
    Returns (consensus_odds, best_odds_per_market, book_comparison).
    - consensus_odds: used for de-vig (Pinnacle if available, else average)
    - best_odds_per_market: {market: {odds, book}} — highest price per market
    - book_comparison: [{name, home, draw, away}] top books for UI display
    """
    home_name = event["home_team"]
    away_name = event["away_team"]
    all_books: dict[str, dict] = {}

    for bm in event.get("bookmakers", []):
        key = bm.get("key", "")
        parsed = _parse_book_odds(bm, home_name, away_name)
        if parsed.get("home"):
            all_books[key] = parsed

    if not all_books:
        return {}, {}, []

    # Consensus: prefer sharp books; fallback to average across all
    consensus: dict = {}
    for sharp in _SHARP_BOOKS:
        if sharp in all_books and all_books[sharp].get("home"):
            consensus = all_books[sharp]
            break
    if not consensus:
        # Average across all books per market
        all_keys = set(k for b in all_books.values() for k in b)
        for mk in all_keys:
            vals = [b[mk] for b in all_books.values() if mk in b]
            if vals:
                consensus[mk] = round(sum(vals) / len(vals), 3)

    # Best odds per market (highest decimal = best for bettor)
    best: dict[str, dict] = {}
    for book_key, prices in all_books.items():
        display = _BOOK_DISPLAY.get(book_key, book_key)
        for mk, price in prices.items():
            if mk not in best or price > best[mk]["odds"]:
                best[mk] = {"odds": round(price, 2), "book": display}

    # Book comparison: top 5 books sorted by home odds desc (for display)
    comparison = sorted(
        [{"name": _BOOK_DISPLAY.get(k, k), **v} for k, v in all_books.items()],
        key=lambda x: x.get("home", 0),
        reverse=True,
    )[:5]

    return consensus, best, comparison


def build_score_predictions(fit, home_model: str, away_model: str) -> tuple[list[dict], dict | None, float, float]:
    try:
        grid = fit.predict_grid(home_model, away_model, neutral=False)
        lam_h, lam_a = fit.expected_goals(home_model, away_model, neutral=False)
    except KeyError:
        return [], None, 0.0, 0.0
    flat = [(h, a, grid.exact_score(h, a)) for h in range(6) for a in range(6)]
    flat.sort(key=lambda x: -x[2])
    top_scores = [{"home": h, "away": a, "prob": round(p * 100, 1)} for h, a, p in flat[:5]]
    # 6x6 grid of raw bivariate probs for heatmap (rows=home goals, cols=away goals)
    score_grid = [[round(grid.exact_score(h, a) * 100, 2) for a in range(6)] for h in range(6)]
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
    return top_scores, quiniela, lam_h, lam_a, score_grid


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

    # ── Per-bet stats (individual bet ROI) ──
    total_individual_bets = 0
    individual_won = 0
    individual_lost = 0
    individual_profit = 0.0
    for fx in fixtures:
        for b in fx.get("bets", []):
            if b.get("won") is None:
                continue
            total_individual_bets += 1
            if b["won"]:
                individual_won += 1
                individual_profit += (b.get("odds", 1.0) - 1.0)
            else:
                individual_lost += 1
                individual_profit -= 1.0

    # Fallback to old best_bet-based counting if bets array not populated yet
    if total_individual_bets == 0:
        bet_fixtures = [fx for fx in fixtures if fx.get("has_bet", fx.get("best_bet") is not None)]
        for fx in bet_fixtures:
            bb = fx.get("best_bet")
            if bb and fx.get("best_bet_won") is not None:
                total_individual_bets += 1
                if fx["best_bet_won"]:
                    individual_won += 1
                    individual_profit += (bb.get("odds", 1.0) - 1.0)
                else:
                    individual_lost += 1
                    individual_profit -= 1.0

    bet_roi = round(individual_profit / total_individual_bets * 100, 1) if total_individual_bets > 0 else 0.0

    # ── Prediction stats ──
    score_hits = sum(1 for fx in fixtures if fx.get("score_hit") is not None)
    score_top1 = sum(1 for fx in fixtures if fx.get("score_hit") == 1)
    correct_1x2 = sum(1 for fx in fixtures if fx.get("correct_1x2") is True)
    total_1x2 = sum(1 for fx in fixtures if fx.get("correct_1x2") is not None)

    history["summary"] = {
        "total": total,
        "total_bets": total_individual_bets,
        "won": individual_won,
        "lost": individual_lost,
        "win_rate": round(individual_won / (individual_won + individual_lost) * 100, 1) if (individual_won + individual_lost) > 0 else 0.0,
        "roi": bet_roi,
        "score_hits": score_hits,
        "score_top1": score_top1,
        "correct_1x2": correct_1x2,
        "total_1x2": total_1x2,
        "accuracy_1x2": round(correct_1x2 / total_1x2 * 100, 1) if total_1x2 > 0 else 0.0,
    }


def _backfill_history(fit) -> None:
    """One-time backfill: add score_hit, 1X2, xG, top_scores, bets to old history entries."""
    history = _load_history()
    changed = False

    for fx in history.get("fixtures", []):
        needs_update = False

        home = fx.get("home", "")
        away = fx.get("away", "")
        home_model = normalize_team(home)
        away_model = normalize_team(away)
        r = fx.get("result", {})
        ah = r.get("home")
        aa = r.get("away")

        # Backfill xG + top_scores if missing
        if fx.get("xg_home") is None and home_model and away_model:
            try:
                top_scores, _, lam_h, lam_a, score_grid = build_score_predictions(fit, home_model, away_model)
                fx["xg_home"] = round(lam_h, 2)
                fx["xg_away"] = round(lam_a, 2)
                fx["top_scores"] = top_scores[:3] if top_scores else []
                fx["score_grid"] = score_grid
                needs_update = True
            except Exception:
                pass

        # Backfill score_hit if missing
        if fx.get("score_hit") is None and ah is not None and fx.get("top_scores"):
            for i, ts in enumerate(fx["top_scores"]):
                if ts["home"] == ah and ts["away"] == aa:
                    fx["score_hit"] = i + 1
                    needs_update = True
                    break

        # Backfill 1X2 prediction if missing
        if fx.get("predicted_1x2") is None and fx.get("xg_home") is not None:
            xh = fx["xg_home"]
            xa = fx["xg_away"]
            fx["predicted_1x2"] = "1" if xh > xa else "2" if xa > xh else "X"
            needs_update = True

        if fx.get("result_1x2") is None and ah is not None and aa is not None:
            fx["result_1x2"] = "1" if ah > aa else "2" if aa > ah else "X"
            needs_update = True

        if fx.get("correct_1x2") is None and fx.get("predicted_1x2") and fx.get("result_1x2"):
            fx["correct_1x2"] = fx["predicted_1x2"] == fx["result_1x2"]
            needs_update = True

        # Backfill individual bets results if missing
        if not fx.get("bets") and fx.get("picks"):
            bets_results = []
            for p in fx["picks"]:
                side = p.get("side") or p.get("market", "")
                won = _side_won(side, ah, aa) if ah is not None else None
                bets_results.append({
                    "market": p.get("market", ""),
                    "side": side,
                    "edge": p.get("edge", 0),
                    "odds": p.get("odds", 1.0),
                    "won": won,
                    "description_es": p.get("market", ""),
                    "description_en": p.get("market", ""),
                })
            if bets_results:
                fx["bets"] = bets_results
                needs_update = True

        if needs_update:
            changed = True

    if changed:
        _update_history_summary(history)
        _save_history(history)
        print(f"[backfill] updated history with prediction data")


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

        # Save ALL picks (BET/SKIP/FADE) for retro-threshold-tuning
        picks = []
        for p in old_fx.get("picks", []):
            side = p.get("side", "")
            won = _side_won(side, actual_home, actual_away) if actual_home is not None else None
            picks.append({
                "market": p["market"],
                "side": side,
                "pick": p["pick"],
                "edge": p["edge"],
                "odds": p["odds"],
                "model_prob": p["model_prob"],
                "devig_prob": p["devig_prob"],
                "won": won,
            })

        # All bets with descriptions (from the bets array if available, else from picks)
        all_bets = old_fx.get("bets", [])
        best_bet = old_fx.get("best_bet")

        # Evaluate each individual bet
        bets_results = []
        for b in (all_bets if all_bets else picks):
            side = b.get("side", "")
            won = _side_won(side, actual_home, actual_away) if actual_home is not None else None
            bets_results.append({
                "market": b.get("market", ""),
                "side": side,
                "edge": b.get("edge", 0),
                "odds": b.get("odds", 1.0),
                "won": won,
                "description_es": b.get("description_es", b.get("market", "")),
                "description_en": b.get("description_en", b.get("market", "")),
            })

        best_bet_won = None
        if best_bet and actual_home is not None and actual_away is not None:
            temp_fx = {
                "best_bet": best_bet,
                "actual_home": actual_home,
                "actual_away": actual_away,
            }
            best_bet_won = _bet_won(temp_fx)

        # Score prediction accuracy
        top_scores = old_fx.get("top_scores", [])
        score_hit = None  # rank (1-5) if exact score matched, else None
        if actual_home is not None and top_scores:
            for i, ts in enumerate(top_scores):
                if ts["home"] == actual_home and ts["away"] == actual_away:
                    score_hit = i + 1
                    break

        # 1X2 prediction accuracy
        xg_home = old_fx.get("xg_home")
        xg_away = old_fx.get("xg_away")
        result_1x2 = None
        predicted_1x2 = None
        if actual_home is not None and actual_away is not None:
            result_1x2 = "1" if actual_home > actual_away else "2" if actual_away > actual_home else "X"
        if xg_home is not None and xg_away is not None:
            predicted_1x2 = "1" if xg_home > xg_away else "2" if xg_away > xg_home else "X"

        history_entry = {
            "id": fx_id,
            "home": home_api,
            "away": away_api,
            "date": old_fx.get("commence_time"),
            "picks": picks,
            "bets": bets_results,
            "best_bet": best_bet,
            "has_bet": best_bet is not None,
            "result": {"home": actual_home, "away": actual_away},
            "best_bet_won": best_bet_won,
            "score_hit": score_hit,
            "top_scores": top_scores[:3] if top_scores else [],
            "xg_home": xg_home,
            "xg_away": xg_away,
            "predicted_1x2": predicted_1x2,
            "result_1x2": result_1x2,
            "correct_1x2": predicted_1x2 == result_1x2 if predicted_1x2 and result_1x2 else None,
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

    # Backfill history with prediction data (runs once, idempotent)
    _backfill_history(fit)

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

        odds, best_odds, book_comparison = extract_odds_from_event(event)
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
                try:
                    ct = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                    elapsed_min = (now - ct).total_seconds() / 60
                    if elapsed_min > 110:
                        # Odds API lag: match ran >110 min, treat as completed
                        completed = True
                        print(f"[auto-complete] {home_api} vs {away_api} ({elapsed_min:.0f}m elapsed)")
                    else:
                        is_live = True
                except Exception:
                    is_live = True

        try:
            pred = predict(fit, home_model, away_model, odds, neutral=False)
        except KeyError:
            continue

        bets = [p for p in pred["picks"] if p["pick"] == "BET"]
        best_bet = max(bets, key=lambda p: p["edge"]) if bets else None

        top_scores, quiniela, lam_h, lam_a, score_grid = build_score_predictions(fit, home_model, away_model)

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
            "best_odds": best_odds,
            "book_comparison": book_comparison,
            "picks": [],
            "best_bet": None,
            "top_scores": top_scores,
            "score_grid": score_grid,
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
                "kelly_pct": round(p.get("kelly_frac", 0) * 100, 2),
            })

        # Build all BET picks with descriptions
        all_bets = []
        for b in sorted(bets, key=lambda p: -p["edge"]):
            b_desc_es, b_desc_en = _pick_description(b["market"], home_api, away_api)
            side_best = best_odds.get(b["side"], {})
            all_bets.append({
                "market": b["market"],
                "side": b["side"],
                "edge": round(b["edge"] * 100, 1),
                "odds": round(b["odds"], 2),
                "best_odds": side_best.get("odds"),
                "best_book": side_best.get("book"),
                "pick": b["pick"],
                "confidence_band": b["confidence_band"],
                "model_prob": round(b["model_prob"] * 100, 1),
                "devig_prob": round(b["devig_prob"] * 100, 1),
                "kelly_pct": round(b.get("kelly_frac", 0) * 100, 2),
                "description_es": b_desc_es,
                "description_en": b_desc_en,
            })
        fixture["bets"] = all_bets

        if best_bet:
            compound = _compound_description(best_bet["side"], bets, home_api, away_api)
            if compound:
                desc_es, desc_en = compound
            else:
                desc_es, desc_en = _pick_description(best_bet["market"], home_api, away_api)
            bb_best = best_odds.get(best_bet["side"], {})
            fixture["best_bet"] = {
                "market": best_bet["market"],
                "side": best_bet["side"],
                "edge": round(best_bet["edge"] * 100, 1),
                "odds": round(best_bet["odds"], 2),
                "best_odds": bb_best.get("odds"),
                "best_book": bb_best.get("book"),
                "pick": best_bet["pick"],
                "confidence_band": best_bet["confidence_band"],
                "model_prob": round(best_bet["model_prob"] * 100, 1),
                "devig_prob": round(best_bet["devig_prob"] * 100, 1),
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
        top_scores, quiniela, _lh, _la, _sg = build_score_predictions(fit, home_model, away_model)
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
            if old_fx["id"] in current_ids:
                continue
            # Carry forward completed fixtures and recently-live ones that
            # may have dropped off the Odds API before being marked complete.
            if old_fx.get("completed") or old_fx.get("is_live"):
                # Auto-complete stale live fixtures: >120 min past commence
                if old_fx.get("is_live") and not old_fx.get("completed"):
                    try:
                        ct = datetime.fromisoformat(old_fx["commence_time"].replace("Z", "+00:00"))
                        if (now - ct).total_seconds() / 60 > 120:
                            old_fx = dict(old_fx)  # don't mutate original
                            old_fx["is_live"] = False
                            old_fx["completed"] = True
                            print(f"[stale-live] {old_fx['home']} vs {old_fx['away']} marked complete")
                    except Exception:
                        pass
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

    standings = fetch_standings()

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
        "standings": standings,
        "fixtures": fixtures,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)
    print(f"output: {OUT} ({len(fixtures)} fixtures)")


def _side_won(side: str, ah: int, aa: int) -> bool:
    """Evaluate if a bet side won given actual scores."""
    if ah is None or aa is None:
        return False
    if side in ("home",) or "Local" in side:
        return ah > aa
    if side in ("away",) or "Visitante" in side:
        return aa > ah
    if side in ("draw",) or "Empate" in side:
        return ah == aa
    if side in ("over_2_5",) or "Over" in side:
        return (ah + aa) > 2.5
    if side in ("under_2_5",) or "Under" in side:
        return (ah + aa) < 2.5
    if side in ("btts_yes",) or ("BTTS" in side and "Sí" in side):
        return ah > 0 and aa > 0
    if side in ("btts_no",) or ("BTTS" in side and "No" in side):
        return ah == 0 or aa == 0
    return False


def _bet_won(fixture: dict) -> bool:
    if not fixture.get("best_bet") or fixture.get("actual_home") is None:
        return False
    bb = fixture["best_bet"]
    side = bb.get("side") or bb.get("market", "")
    return _side_won(side, fixture["actual_home"], fixture["actual_away"])


if __name__ == "__main__":
    generate()
