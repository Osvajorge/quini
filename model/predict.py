"""End-to-end prediction: fit → markets → edge → picks.

Ensemble architecture (Benter-inspired):
  1. Dixon-Coles goal model → raw 1X2 + derivative markets
  2. Elo regularizer → stabilizes 1X2 for low-sample teams
  3. Market blend → log-odds combination with devigged bookmaker probs
  4. Draw inflation → tournament-specific correction for draw rate
  5. Form factor → recent-results momentum adjustment

Benter (1994) showed that combining a fundamental model with public odds
via log-odds (not linear blend) captures ~2% additional R² and is the key
to profitability. We adapt his conditional logit approach for football:
  c_i = exp(α·ln(f_i) + β·ln(π_i)) / Σ exp(α·ln(f_j) + β·ln(π_j))
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from model.devig import devig_shin
from model.bivariate_poisson import BPFit, load_fit
from model.elo import compute_ratings as elo_compute_ratings, match_probs as elo_match_probs

EDGE_THRESHOLD = 0.08
EDGE_SCORE_CAP = 0.05
SAMPLE_FULL_SUPPORT = 30
MIN_MODEL_PROB = 0.30
EV_THRESHOLD = 0.04
ELO_BLEND_WEIGHT = 0.30
BAYESIAN_UPDATE_WEIGHT = 0.15

# Benter blend: α weights fundamental model, β weights market.
# α > β means we trust our model more than the market.
# Benter found α≈1.0, β≈0.8 for horse racing; football markets are
# more efficient, so we use α=0.6, β=0.4 (trust market more).
BENTER_ALPHA = 0.60
BENTER_BETA = 0.40

# Draw inflation: WC2026 group stage has ~23% draws (10/44).
# Dixon-Coles rho helps but still underestimates. This additive
# boost to draw probability comes from observed tournament draw rate.
DRAW_INFLATION = 0.03

# Form momentum: how much recent form (last 5 matches) adjusts xG.
# 0.0 = ignore form, 0.15 = ±15% xG adjustment for perfect/terrible form.
FORM_WEIGHT = 0.15

PER_MARKET_THRESHOLDS: dict[str, float] = {
    "1x2": 0.05,
    "ou":  0.05,
    "btts": 0.06,
    "ah":  0.07,
}
# All thresholds derived from tools/tune_threshold.py on history.json.
# Re-run after every cron tick to keep them honest.


def _market_threshold(side: str) -> float:
    """Map a market side key to its per-market threshold."""
    s = side.lower() if isinstance(side, str) else ""
    if s in ("home", "draw", "away"):
        return PER_MARKET_THRESHOLDS["1x2"]
    if s.startswith("over") or s.startswith("under"):
        return PER_MARKET_THRESHOLDS["ou"]
    if s.startswith("btts"):
        return PER_MARKET_THRESHOLDS["btts"]
    if s.startswith("ah_"):
        return PER_MARKET_THRESHOLDS["ah"]
    return EDGE_THRESHOLD

_ELO_CACHE: dict[str, float] | None = None
_FORM_CACHE: dict[str, float] | None = None


def _get_elo_ratings() -> dict[str, float]:
    global _ELO_CACHE
    if _ELO_CACHE is None:
        _ELO_CACHE = elo_compute_ratings()
    return _ELO_CACHE


def _compute_form(n: int = 5) -> dict[str, float]:
    """Recent form score per team: goals scored vs expected in last n matches.

    Returns a multiplier centered on 1.0:
      >1.0 = overperforming (hot streak)
      <1.0 = underperforming (cold streak)
    Capped to [1-FORM_WEIGHT, 1+FORM_WEIGHT].
    """
    import pandas as pd
    from model.data_loader import DATA_PATH

    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    df = df.dropna(subset=["home_score", "away_score"]).sort_values("date")
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)

    form: dict[str, list[float]] = {}
    for _, row in df.iterrows():
        h, a = row["home_team"], row["away_team"]
        hs, as_ = row["home_score"], row["away_score"]
        # Points: win=3, draw=1, loss=0, normalized to [0,1]
        hp = 1.0 if hs > as_ else (0.33 if hs == as_ else 0.0)
        ap = 1.0 if as_ > hs else (0.33 if hs == as_ else 0.0)
        form.setdefault(h, []).append(hp)
        form.setdefault(a, []).append(ap)

    result = {}
    for team, pts in form.items():
        recent = pts[-n:] if len(pts) >= n else pts
        avg = sum(recent) / len(recent) if recent else 0.5
        # Map [0, 1] → [1-FORM_WEIGHT, 1+FORM_WEIGHT]
        result[team] = 1.0 + FORM_WEIGHT * (avg - 0.5) * 2
    return result


def _get_form() -> dict[str, float]:
    global _FORM_CACHE
    if _FORM_CACHE is None:
        _FORM_CACHE = _compute_form()
    return _FORM_CACHE


def _form_adjusted_grid(fit: BPFit, lam_h: float, lam_a: float, max_goals: int = 10):
    """Rebuild FootballProbabilityGrid with form-adjusted lambdas + rho."""
    from scipy.stats import poisson
    from penaltyblog.models.football_probability_grid import FootballProbabilityGrid

    n = max_goals + 1
    mat = np.outer(poisson.pmf(range(n), lam_h), poisson.pmf(range(n), lam_a))

    rho = fit.model.get_params().get("rho", 0.0)
    if rho != 0.0:
        if mat[0, 0] > 0:
            mat[0, 0] *= 1 - lam_h * lam_a * rho
        if mat[0, 1] > 0:
            mat[0, 1] *= 1 + lam_h * rho
        if mat[1, 0] > 0:
            mat[1, 0] *= 1 + lam_a * rho
        if mat[1, 1] > 0:
            mat[1, 1] *= 1 - rho

    return FootballProbabilityGrid(mat, lam_h, lam_a)


_BAYES_CACHE: dict[str, float] | None = None


def _compute_bayesian_updates() -> dict[str, float]:
    """Bayesian posterior update from WC2026 results.

    Each match shifts team strength based on observed vs expected goals.
    Teams that outperform xG get boosted; underperformers get penalized.
    Inspired by Ridall et al. (2025) Bayesian state-space model.
    """
    import json
    hist_path = Path(__file__).resolve().parent.parent / "docs" / "data" / "history.json"
    if not hist_path.exists():
        return {}

    with open(hist_path) as f:
        hist = json.load(f)

    adjustments: dict[str, list[float]] = {}
    for hfx in hist["fixtures"]:
        r = hfx.get("result")
        if not r:
            continue
        home, away = hfx["home"], hfx["away"]
        hg, ag = r["home"], r["away"]
        xg_h = hfx.get("xg_home") or hg
        xg_a = hfx.get("xg_away") or ag

        # Ratio of actual goals to expected — >1 = overperforming
        ratio_h = (hg + 0.5) / (xg_h + 0.5)
        ratio_a = (ag + 0.5) / (xg_a + 0.5)
        adjustments.setdefault(home, []).append(ratio_h)
        adjustments.setdefault(away, []).append(ratio_a)

    result = {}
    for team, ratios in adjustments.items():
        # Exponential recency: last match weighs most
        weights = [0.5 ** i for i in range(len(ratios) - 1, -1, -1)]
        w_sum = sum(weights)
        avg = sum(r * w for r, w in zip(ratios, weights)) / w_sum
        result[team] = max(0.85, min(1.15, avg))
    return result


def _get_bayesian_updates() -> dict[str, float]:
    global _BAYES_CACHE
    if _BAYES_CACHE is None:
        _BAYES_CACHE = _compute_bayesian_updates()
    return _BAYES_CACHE


def _benter_blend(model_probs: dict[str, float], market_probs: dict[str, float],
                  alpha: float = BENTER_ALPHA, beta: float = BENTER_BETA) -> dict[str, float]:
    """Benter (1994) log-odds combination of fundamental model + market.

    c_i = exp(α·ln(f_i) + β·ln(π_i)) / Σ_j exp(α·ln(f_j) + β·ln(π_j))

    This is superior to linear blending because it operates in log-odds
    space, naturally handling the multiplicative nature of probability ratios.
    """
    keys = list(model_probs.keys())
    eps = 1e-8
    raw = {}
    for k in keys:
        f = max(model_probs[k], eps)
        p = max(market_probs.get(k, 1.0 / len(keys)), eps)
        raw[k] = math.exp(alpha * math.log(f) + beta * math.log(p))
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def _inflate_draws(probs: dict[str, float], inflation: float = DRAW_INFLATION) -> dict[str, float]:
    """Boost draw probability by a fixed amount, redistributing from H/A."""
    if "draw" not in probs or inflation <= 0:
        return probs
    p = dict(probs)
    boost = min(inflation, 0.15)
    p["draw"] += boost
    # Redistribute proportionally from home and away
    ha_total = p["home"] + p["away"]
    if ha_total > boost:
        ratio = (ha_total - boost) / ha_total
        p["home"] *= ratio
        p["away"] *= ratio
    total = sum(p.values())
    return {k: v / total for k, v in p.items()}


def attack_defense_power(fit: BPFit, team: str) -> dict[str, float] | None:
    """Extract attack/defense strength from fitted Dixon-Coles parameters."""
    if team not in fit.teams:
        return None
    params = fit.model.get_params()
    return {
        "attack": params.get(f"attack_{team}", 0.0),
        "defense": params.get(f"defence_{team}", params.get(f"defense_{team}", 0.0)),
    }

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
    kelly_frac: float = 0.0  # quarter-Kelly fraction of bankroll, capped at 5%


def kelly_fraction(model_prob: float, odds: float, kelly_mult: float = 0.25, cap: float = 0.05) -> float:
    """Quarter-Kelly bankroll fraction, capped at 5%.

    Full Kelly is aggressive — quarter-Kelly gives ~50% of long-run growth
    with much less variance. Cap protects against model overconfidence.
    """
    b = odds - 1.0
    if b <= 0 or model_prob <= 0:
        return 0.0
    f = (b * model_prob - (1 - model_prob)) / b
    if f <= 0:
        return 0.0
    return min(f * kelly_mult, cap)


def _market_group(side: str) -> str:
    """Group key for side alignment — picks within same group compete for BET."""
    s = (side or "").lower()
    if s in ("home", "draw", "away"):
        return "1x2"
    if s.startswith("over_") or s.startswith("under_"):
        # Group same total: over_2_5 / under_2_5 are a group
        return "ou_" + s.split("_", 1)[1]
    if s.startswith("btts"):
        return "btts"
    if s.startswith("ah_"):
        # ah_home_-1_5 / ah_away_+1_5 share group via line
        return "ah_" + s.split("_", 2)[-1] if "_" in s else "ah"
    return s


def _align_bets_to_top(picks: list) -> None:
    """Downgrade BETs that aren't the model's top pick in their market group.

    Rule: in each market group (1X2, O/U 2.5, BTTS, etc.), only the side with
    the highest model probability is allowed to remain BET. Other sides that
    were classified BET get downgraded to SKIP.

    This aligns user expectation ("modelo dice X → apuesta X") with the
    actual recommendation, and historically improves win rate substantially
    (favorites win more than longshot-edges materialize).
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for p in picks:
        groups[_market_group(p.side)].append(p)
    for group_picks in groups.values():
        if len(group_picks) <= 1:
            continue
        top = max(group_picks, key=lambda p: p.model_prob)
        for p in group_picks:
            if p.pick == "BET" and p.side != top.side:
                p.pick = "SKIP"


MAX_1X2_ODDS = 5.0

def _classify(edge: float, model_prob: float = 0.0, odds: float = 1.0, side: str = "") -> PickLabel:
    """BET requires per-market edge threshold AND model confidence AND positive EV.

    Structural guards from WC2026 backtest (0W-10L on draws, longshot 1X2 traps):
      - Draw bets blocked: 0/10 hit rate despite avg +3.4% edge
      - 1X2 longshots (odds > 5) blocked: model overestimates edge on weak teams
    """
    s = side.lower() if isinstance(side, str) else ""
    if s == "draw":
        return "SKIP"
    if s in ("home", "away") and odds > MAX_1X2_ODDS:
        return "SKIP"
    thr = _market_threshold(side)
    if edge >= thr and model_prob >= MIN_MODEL_PROB:
        ev = model_prob * odds - 1.0
        if ev >= EV_THRESHOLD:
            return "BET"
    if edge <= -thr:
        return "FADE"
    return "SKIP"


def _confidence(edge: float, sample_score: float, side: str = "") -> tuple[float, str]:
    thr = _market_threshold(side) if side else EDGE_THRESHOLD
    edge_score = max(0.0, min((edge - thr) / EDGE_SCORE_CAP, 1.0))
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


TEAM_ALIASES: dict[str, str] = {
    "USA": "United States",
    "Czechia": "Czech Republic",
    "Czech Republic": "Czech Republic",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Korea Republic": "South Korea",
    "Ivory Coast": "Côte d'Ivoire",
    "DR Congo": "Congo DR",
    "Türkiye": "Turkey",
    "Curaçao": "Curaçao",
    "Cape Verde": "Cabo Verde",
}


def _resolve_team(name: str, teams: list[str]) -> str:
    """Resolve team name to the canonical form in the fitted model."""
    if name in teams:
        return name
    alias = TEAM_ALIASES.get(name)
    if alias and alias in teams:
        return alias
    for t in teams:
        if t.lower() == name.lower():
            return t
    return name


def predict(
    fit: BPFit,
    home: str,
    away: str,
    odds: dict,
    neutral: bool = False,
    max_goals: int = 10,
) -> dict:
    """Produce xG, score matrix and per-side picks via Dixon-Coles.

    `odds` keys are flexible — supply only the markets you have prices for:
      {"home", "draw", "away"}        → 1X2
      {"over_2_5", "under_2_5"}       → O/U 2.5
      {"btts_yes", "btts_no"}         → BTTS
      {"ah_home_-1_5", "ah_away_+1_5"} → Asian handicap -1.5 / +1.5
    """
    home = _resolve_team(home, fit.teams)
    away = _resolve_team(away, fit.teams)

    # ── Form + Bayesian update: scale xG by momentum + tournament performance ──
    form = _get_form()
    form_h = form.get(home, 1.0)
    form_a = form.get(away, 1.0)

    bayes = _get_bayesian_updates()
    bayes_h = bayes.get(home, 1.0)
    bayes_a = bayes.get(away, 1.0)

    grid = fit.predict_grid(home, away, neutral=neutral)
    lam_h_raw, lam_a_raw = fit.expected_goals(home, away, neutral=neutral)
    adj_h = form_h * (1.0 + BAYESIAN_UPDATE_WEIGHT * (bayes_h - 1.0))
    adj_a = form_a * (1.0 + BAYESIAN_UPDATE_WEIGHT * (bayes_a - 1.0))
    lam_h = lam_h_raw * adj_h
    lam_a = lam_a_raw * adj_a

    # Rebuild grid with adjusted lambdas + Dixon-Coles rho
    if adj_h != 1.0 or adj_a != 1.0:
        grid = _form_adjusted_grid(fit, lam_h, lam_a, max_goals)

    under25, _, over25 = grid.totals(2.5)
    under35, _, over35 = grid.totals(3.5)

    # ── Ensemble 1X2: Dixon-Coles + Elo regularizer ──
    bp_1x2 = {"home": grid.home_win, "draw": grid.draw, "away": grid.away_win}
    elo_ratings = _get_elo_ratings()
    rh = elo_ratings.get(home)
    ra = elo_ratings.get(away)
    sample_s = _sample_score(fit, home, away)
    if rh is not None and ra is not None:
        elo_probs = elo_match_probs(rh, ra, neutral=neutral)
        w = ELO_BLEND_WEIGHT + (0.50 - ELO_BLEND_WEIGHT) * (1.0 - sample_s)
        ensemble_1x2 = {
            k: (1 - w) * bp_1x2[k] + w * elo_probs[k]
            for k in ("home", "draw", "away")
        }
        s = sum(ensemble_1x2.values())
        ensemble_1x2 = {k: v / s for k, v in ensemble_1x2.items()}
    else:
        ensemble_1x2 = bp_1x2

    # ── Draw inflation: correct systematic underestimation ──
    ensemble_1x2 = _inflate_draws(ensemble_1x2)

    model_probs = {
        "1x2": ensemble_1x2,
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

    # ── Benter blend: combine model probs with market probs per group ──
    # For each devig group, create a blended probability using log-odds.
    # The blended prob becomes our "true" estimate; edge = blend - devig.
    benter_cache: dict[tuple[str, ...], dict[str, float]] = {}
    for group, devig_probs in devig_cache.items():
        # Gather model probs for this group
        group_model: dict[str, float] = {}
        for key, (_, g, path) in market_map.items():
            if g != group:
                continue
            top, sub = path.split(".")
            group_model[key] = float(model_probs[top][sub])
        if group_model:
            benter_cache[group] = _benter_blend(group_model, devig_probs)

    picks: list[MarketPick] = []
    for key, (label, group, path) in market_map.items():
        if key not in odds or group not in devig_cache:
            continue
        top, sub = path.split(".")
        raw_model_p = float(model_probs[top][sub])
        devig_p = float(devig_cache[group][key])
        blended_p = benter_cache.get(group, {}).get(key, raw_model_p)
        edge = blended_p - devig_p
        pick_lbl = _classify(edge, blended_p, odds[key], side=key)
        raw, band = _confidence(edge, sample_s, side=key)
        kf = kelly_fraction(blended_p, odds[key]) if pick_lbl == "BET" else 0.0
        picks.append(MarketPick(
            market=label,
            side=key,
            model_prob=blended_p,
            devig_prob=devig_p,
            edge=edge,
            odds=odds[key],
            pick=pick_lbl,
            confidence_raw=raw,
            confidence_band=band,
            kelly_frac=kf,
        ))

    # ── Align BETs to model's top pick per market group ──
    # Without this, the model can recommend BET on a longshot (e.g. Norway 35.6%
    # when France 40.6% is the favorite) just because the longshot has +edge.
    # User-facing rule: only BET when the side is the model's favorite within
    # its market group AND has positive edge. Eliminates the cognitive
    # dissonance of "modelo dice X, pero apuesta Y".
    _align_bets_to_top(picks)

    # Recompute kelly_frac after alignment (downgraded BETs → 0)
    for p in picks:
        if p.pick != "BET":
            p.kelly_frac = 0.0

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
        "form": {"home": round(form_h, 3), "away": round(form_a, 3)},
        "bayesian": {"home": round(bayes_h, 3), "away": round(bayes_a, 3)},
        "power": {
            "home": attack_defense_power(fit, home),
            "away": attack_defense_power(fit, away),
        },
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
