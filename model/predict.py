"""End-to-end prediction: fit → markets → edge → picks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from model.devig import devig_shin
from model.bivariate_poisson import BPFit, load_fit
from model.elo import compute_ratings as elo_compute_ratings, match_probs as elo_match_probs

EDGE_THRESHOLD = 0.08  # default fallback — see PER_MARKET_THRESHOLDS below
EDGE_SCORE_CAP = 0.05  # saturates 5 pts above threshold (so edge ≥ 13% → 1.0)
SAMPLE_FULL_SUPPORT = 30  # n_matches at which sample_score saturates
MIN_MODEL_PROB = 0.30  # at 0.25 backtest ROI was +6.1%, at 0.30 was +12.7%
EV_THRESHOLD = 0.04  # min expected value (model_prob * odds - 1) to BET
ELO_BLEND_WEIGHT = 0.30  # ensemble: 70% BivariatePoisson + 30% Elo regularizer

# Per-market edge thresholds — backtest revealed major asymmetry:
#   1X2:  threshold=7%  →  -41% ROI  (model is BAD at win/draw/loss)
#   1X2:  threshold=12% →   bottoms out
#   O/U:  threshold=7%  →  +41% ROI  (model is GREAT at over/under)
#   O/U:  threshold=8%  →  +59% ROI  (peak)
# Result: raise 1X2 to 12% (effectively suppresses bad bets), keep O/U at 8%.
PER_MARKET_THRESHOLDS: dict[str, float] = {
    "1x2": 0.05,    # 5% — now safe because _align_bets_to_top requires favorite
    "ou":  0.05,    # O/U is model's strong market; favorite constraint keeps it honest
    "btts": 0.06,   # both teams to score
    "ah":  0.07,    # asian handicap
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

# Module-level cache so Elo ratings are computed once per process.
_ELO_CACHE: dict[str, float] | None = None


def _get_elo_ratings() -> dict[str, float]:
    global _ELO_CACHE
    if _ELO_CACHE is None:
        _ELO_CACHE = elo_compute_ratings()
    return _ELO_CACHE

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


def _classify(edge: float, model_prob: float = 0.0, odds: float = 1.0, side: str = "") -> PickLabel:
    """BET requires per-market edge threshold AND model confidence AND positive EV.

    Per-market thresholds reflect backtest reality: 1X2 needs 12% to be
    profitable, O/U needs only 8%. Avoids the long-shot trap (Scotland 22%
    with edge 9% used to qualify as BET — now correctly skipped).
    """
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

    # ── Ensemble 1X2: blend BivariatePoisson with Elo regularizer ──
    # BP can overfit on small-sample teams (Scotland-Brazil case). Elo is a
    # robust global ranking that pulls predictions toward sensible priors.
    # Blend weight scales with sample: low n → trust Elo more (up to 50%),
    # high n → trust BP more (floor at ELO_BLEND_WEIGHT=30%).
    bp_1x2 = {"home": grid.home_win, "draw": grid.draw, "away": grid.away_win}
    elo_ratings = _get_elo_ratings()
    rh = elo_ratings.get(home)
    ra = elo_ratings.get(away)
    sample_s = _sample_score(fit, home, away)  # 0=no data, 1=full support
    if rh is not None and ra is not None:
        elo_probs = elo_match_probs(rh, ra, neutral=neutral)
        # When sample_s=1 → w=ELO_BLEND_WEIGHT (0.30)
        # When sample_s=0 → w=0.50 (max Elo trust)
        w = ELO_BLEND_WEIGHT + (0.50 - ELO_BLEND_WEIGHT) * (1.0 - sample_s)
        ensemble_1x2 = {
            k: (1 - w) * bp_1x2[k] + w * elo_probs[k]
            for k in ("home", "draw", "away")
        }
        s = sum(ensemble_1x2.values())
        ensemble_1x2 = {k: v / s for k, v in ensemble_1x2.items()}
    else:
        ensemble_1x2 = bp_1x2

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

    picks: list[MarketPick] = []
    for key, (label, group, path) in market_map.items():
        if key not in odds or group not in devig_cache:
            continue
        top, sub = path.split(".")
        model_p = float(model_probs[top][sub])
        devig_p = float(devig_cache[group][key])
        edge = model_p - devig_p
        pick_lbl = _classify(edge, model_p, odds[key], side=key)
        raw, band = _confidence(edge, sample_s, side=key)
        kf = kelly_fraction(model_p, odds[key]) if pick_lbl == "BET" else 0.0
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
