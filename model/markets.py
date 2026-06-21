"""Score matrix + market derivations from Dixon-Coles parameters."""
from __future__ import annotations

import numpy as np
from scipy.special import gammaln


def _poisson_pmf(k: np.ndarray, lam: float) -> np.ndarray:
    lam = max(lam, 1e-10)
    return np.exp(k * np.log(lam) - lam - gammaln(k + 1.0))


def score_matrix(
    lam_h: float, lam_a: float, rho: float, max_goals: int = 10
) -> np.ndarray:
    """Return P[h, a] joint score distribution with DC low-score correction.

    Shape: (max_goals+1, max_goals+1). Rows = home goals, cols = away goals.
    Normalised to sum to 1 (tail truncation beyond max_goals).
    """
    k = np.arange(max_goals + 1)
    ph = _poisson_pmf(k, lam_h)
    pa = _poisson_pmf(k, lam_a)
    m = np.outer(ph, pa)

    # Dixon-Coles correction on the (0,0), (0,1), (1,0), (1,1) cells.
    m[0, 0] *= 1.0 - lam_h * lam_a * rho
    m[0, 1] *= 1.0 + lam_h * rho
    m[1, 0] *= 1.0 + lam_a * rho
    m[1, 1] *= 1.0 - rho

    m = np.clip(m, 0.0, None)
    s = m.sum()
    if s <= 0:
        raise ValueError("Degenerate score matrix.")
    return m / s


def prob_1x2(m: np.ndarray) -> tuple[float, float, float]:
    n = m.shape[0]
    h_idx, a_idx = np.indices((n, n))
    home = m[h_idx > a_idx].sum()
    draw = m[h_idx == a_idx].sum()
    away = m[h_idx < a_idx].sum()
    return float(home), float(draw), float(away)


def prob_over_under(m: np.ndarray, line: float = 2.5) -> tuple[float, float]:
    n = m.shape[0]
    h_idx, a_idx = np.indices((n, n))
    totals = h_idx + a_idx
    over = m[totals > line].sum()
    under = m[totals < line].sum()
    # Half-line so push is impossible; integer line would need a push bucket.
    return float(over), float(under)


def prob_btts(m: np.ndarray) -> tuple[float, float]:
    n = m.shape[0]
    h_idx, a_idx = np.indices((n, n))
    yes = m[(h_idx > 0) & (a_idx > 0)].sum()
    return float(yes), float(1.0 - yes)


def prob_handicap(m: np.ndarray, line: float = -1.5) -> tuple[float, float]:
    """Asian handicap on the home side.

    line = -1.5 → home must win by ≥ 2.
    line = +1.5 → home can lose by ≤ 1 (i.e. lose by 1, draw or win).
    Half-lines only; no push.
    """
    n = m.shape[0]
    h_idx, a_idx = np.indices((n, n))
    margin = h_idx - a_idx  # home - away
    home_cover = m[margin + line > 0].sum()
    away_cover = m[margin + line < 0].sum()
    return float(home_cover), float(away_cover)


def all_markets(m: np.ndarray) -> dict:
    h, d, a = prob_1x2(m)
    o25, u25 = prob_over_under(m, 2.5)
    o35, u35 = prob_over_under(m, 3.5)
    btts_y, btts_n = prob_btts(m)
    hcp_h_15, hcp_a_15 = prob_handicap(m, -1.5)
    hcp_h_p15, hcp_a_p15 = prob_handicap(m, +1.5)
    return {
        "1x2": {"home": h, "draw": d, "away": a},
        "ou_2_5": {"over": o25, "under": u25},
        "ou_3_5": {"over": o35, "under": u35},
        "btts": {"yes": btts_y, "no": btts_n},
        "ah_home_-1_5": {"home_cover": hcp_h_15, "away_cover": hcp_a_15},
        "ah_home_+1_5": {"home_cover": hcp_h_p15, "away_cover": hcp_a_p15},
    }


if __name__ == "__main__":
    from model.dixon_coles import load_fit

    fit = load_fit()
    cases = [
        ("Spain", "Cape Verde", False),
        ("Brazil", "Morocco", False),
        ("Argentina", "Algeria", False),
        ("Mexico", "United States", True),
        ("Germany", "Curaçao", False),
    ]
    for h, a, neu in cases:
        try:
            lh, la = fit.expected_goals(h, a, neutral=neu)
        except KeyError as e:
            print(f"skip {h} vs {a}: {e}")
            continue
        m = score_matrix(lh, la, fit.rho)
        assert abs(m.sum() - 1.0) < 1e-9, f"matrix not normalised: {m.sum()}"
        markets = all_markets(m)

        print(f"\n{h} vs {a} (neutral={neu}) · xG {lh:.2f}-{la:.2f}")
        m1x2 = markets["1x2"]
        print(f"  1X2 : {m1x2['home']*100:5.1f}% / {m1x2['draw']*100:5.1f}% / {m1x2['away']*100:5.1f}%")
        print(f"  O2.5: {markets['ou_2_5']['over']*100:5.1f}%   BTTS: {markets['btts']['yes']*100:5.1f}%")
        print(f"  AH -1.5 home: {markets['ah_home_-1_5']['home_cover']*100:5.1f}%")
        # Sanity sums
        s1x2 = m1x2['home'] + m1x2['draw'] + m1x2['away']
        sou = markets['ou_2_5']['over'] + markets['ou_2_5']['under']
        sbtts = markets['btts']['yes'] + markets['btts']['no']
        assert abs(s1x2 - 1.0) < 1e-6
        assert abs(sou - 1.0) < 1e-6
        assert abs(sbtts - 1.0) < 1e-6
