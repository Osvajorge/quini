"""End-to-end prediction: fit → markets → edge → picks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from model.devig import devig_shin
from model.bivariate_poisson import BPFit, load_fit

EDGE_THRESHOLD = 0.07  # 7%
EDGE_SCORE_CAP = 0.05  # saturates 5 pts above threshold (so edge ≥ 12% → 1.0)
SAMPLE_FULL_SUPPORT = 30  # n_matches at which sample_score saturates

PickLabel = Literal["BET", "SKIP", "FADE"]


@dataclass
class MarketPick:
    market: str
    side: str
    model_prob: float
    devig_prob: float
    edge: float
    odds: float
    pick: PickLabel
    confidence_raw: float
    confidence_band: Literal["ALTA", "MEDIA", "BAJA"]


def _classify(edge: float) -> PickLabel:
    if edge >= EDGE_THRESHOLD:
        return "BET"
    if edge <= -EDGE_THRESHOLD:
        return "FADE"
    return "SKIP"


def _confidence(edge: float, sample_score: float) -> tuple[float, str]:
    edge_score = max(0.0, min((edge - EDGE_THRESHOLD) / EDGE_SCORE_CAP, 1.0))
    raw = 0.6 * edge_score + 0.4 * sample_score
    if raw >= 0.7:
        band = "ALTA"
    elif raw >= 0.4:
        band = "MEDIA"
    else:
        band = "BAJA"
    return raw, band


def _sample_score(fit: BPFit, home: str, away: str) -> float:
    n_h = fit.match_counts.get(home, 0)
    n_a = fit.match_counts.get(away, 0)
    return min(min(n_h, n_a) / SAMPLE_FULL_SUPPORT, 1.0)


def predict(
    fit: BPFit,
    home: str,
    away: str,
    odds: dict,
    neutral: bool = False,
    max_goals: int = 10,
) -> dict:
    """Produce xG, score matrix and per-side picks via Bivariate Poisson.

    `odds` keys are flexible — supply only the markets you have prices for:
      {"home", "draw", "away"}        → 1X2
      {"over_2_5", "under_2_5"}       → O/U 2.5
      {"btts_yes", "btts_no"}         → BTTS
      {"ah_home_-1_5", "ah_away_+1_5"} → Asian handicap -1.5 / +1.5
    """
    grid = fit.predict_grid(home, away, neutral=neutral)
    lam_h, lam_a = fit.expected_goals(home, away, neutral=neutral)

    under25, _, over25 = grid.totals(2.5)
    under35, _, over35 = grid.totals(3.5)
    model_probs = {
        "1x2": {"home": grid.home_win, "draw": grid.draw, "away": grid.away_win},
        "ou_2_5": {"over": over25, "under": under25},
        "ou_3_5": {"over": over35, "under": under35},
        "btts": {"yes": grid.btts_yes, "no": grid.btts_no},
        "ah_home_-1_5": {
            "home_cover": grid.asian_handicap("home", -1.5),
            "away_cover": grid.asian_handicap("away", -1.5),
        },
        "ah_home_+1_5": {
            "home_cover": grid.asian_handicap("home", 1.5),
            "away_cover": grid.asian_handicap("away", 1.5),
        },
    }
    matrix = fit.score_matrix(home, away, neutral=neutral, n=max_goals + 1)
    sample_s = _sample_score(fit, home, away)

    market_map: dict[str, tuple[str, tuple[str, str], str]] = {
        # market key → (side label, (devig group), model_probs path)
        "home": ("Gana Local (1)", ("home", "draw", "away"), "1x2.home"),
        "draw": ("Empate (X)", ("home", "draw", "away"), "1x2.draw"),
        "away": ("Gana Visitante (2)", ("home", "draw", "away"), "1x2.away"),
        "over_2_5": ("Over 2.5", ("over_2_5", "under_2_5"), "ou_2_5.over"),
        "under_2_5": ("Under 2.5", ("over_2_5", "under_2_5"), "ou_2_5.under"),
        "btts_yes": ("BTTS Sí", ("btts_yes", "btts_no"), "btts.yes"),
        "btts_no": ("BTTS No", ("btts_yes", "btts_no"), "btts.no"),
        "ah_home_-1_5": ("Hándicap Local -1.5", ("ah_home_-1_5", "ah_away_+1_5"), "ah_home_-1_5.home_cover"),
        "ah_away_+1_5": ("Hándicap Visit +1.5", ("ah_home_-1_5", "ah_away_+1_5"), "ah_home_-1_5.away_cover"),
    }

    # De-vig per group, only for groups whose odds are fully supplied.
    devig_cache: dict[tuple[str, ...], dict[str, float]] = {}
    for key, (_, group, _) in market_map.items():
        if group in devig_cache:
            continue
        if all(g in odds for g in group):
            group_odds = [odds[g] for g in group]
            try:
                p = devig_shin(group_odds)
            except Exception:
                continue
            devig_cache[group] = {g: float(p[i]) for i, g in enumerate(group)}

    picks: list[MarketPick] = []
    for key, (label, group, path) in market_map.items():
        if key not in odds or group not in devig_cache:
            continue
        top, sub = path.split(".")
        model_p = float(model_probs[top][sub])
        devig_p = float(devig_cache[group][key])
        edge = model_p - devig_p
        pick_lbl = _classify(edge)
        raw, band = _confidence(edge, sample_s)
        picks.append(MarketPick(
            market=label,
            side=key,
            model_prob=model_p,
            devig_prob=devig_p,
            edge=edge,
            odds=odds[key],
            pick=pick_lbl,
            confidence_raw=raw,
            confidence_band=band,
        ))

    # Overall fixture confidence = best BET on the slate.
    bets = [p for p in picks if p.pick == "BET"]
    if bets:
        best = max(bets, key=lambda p: p.confidence_raw)
        fixture_conf = best.confidence_band
    else:
        fixture_conf = "BAJA"

    return {
        "home": home,
        "away": away,
        "neutral": neutral,
        "xg_home": lam_h,
        "xg_away": lam_a,
        "model_probs": model_probs,
        "score_matrix": matrix.tolist(),
        "picks": [p.__dict__ for p in picks],
        "fixture_confidence": fixture_conf,
        "sample_score": sample_s,
    }


if __name__ == "__main__":
    fit = load_fit()

    odds_spain_cv = {
        "home": 1.25, "draw": 6.5, "away": 13.0,
        "over_2_5": 1.55, "under_2_5": 2.45,
        "btts_yes": 2.40, "btts_no": 1.55,
        "ah_home_-1_5": 1.85, "ah_away_+1_5": 1.95,
    }
    out = predict(fit, "Spain", "Cape Verde", odds_spain_cv, neutral=False)
    print(f"\n{out['home']} vs {out['away']} · xG {out['xg_home']:.2f}-{out['xg_away']:.2f}")
    print(f"fixture confidence: {out['fixture_confidence']} · sample={out['sample_score']:.2f}")
    print(f"\n{'Mercado':24} {'Modelo':>8} {'Devig':>8} {'Edge':>8} {'Odds':>6}  Pick   Conf")
    print("-" * 80)
    for p in out["picks"]:
        print(
            f"{p['market']:24} "
            f"{p['model_prob']*100:7.1f}% "
            f"{p['devig_prob']*100:7.1f}% "
            f"{p['edge']*100:+7.1f}% "
            f"{p['odds']:6.2f}  "
            f"{p['pick']:5}  {p['confidence_band']}"
        )
