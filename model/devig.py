"""Remove bookmaker overround from decimal odds.

Two methods:
- proportional: divide each implied prob by the overround. Fast, biased
  towards overestimating favourites.
- shin: solves for insider-trading proportion z. Less biased on extreme
  favourites. Standard reference: Shin (1993).
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def implied_probs(odds: list[float]) -> np.ndarray:
    arr = np.asarray(odds, dtype=float)
    if (arr <= 1.0).any():
        raise ValueError("Decimal odds must be > 1.")
    return 1.0 / arr


def devig_proportional(odds: list[float]) -> np.ndarray:
    p = implied_probs(odds)
    return p / p.sum()


def _shin_probs(z: float, b: np.ndarray) -> np.ndarray:
    # Shin closed-form per outcome given z.
    sb = b.sum()
    inside = z * z + 4.0 * (1.0 - z) * (b * b) / sb
    inside = np.clip(inside, 0.0, None)
    return (np.sqrt(inside) - z) / (2.0 * (1.0 - z))


def devig_shin(odds: list[float], tol: float = 1e-10) -> np.ndarray:
    """Shin (1993). Solves for z s.t. sum(p) = 1."""
    b = implied_probs(odds)
    if b.sum() <= 1.0:
        # No overround → already de-vigged. Just normalise.
        return b / b.sum()

    def gap(z: float) -> float:
        return _shin_probs(z, b).sum() - 1.0

    # gap is monotonic in z on (0, 1); bracket and solve.
    lo, hi = 1e-12, 1.0 - 1e-6
    if gap(lo) * gap(hi) > 0:
        # Fallback: gap doesn't cross zero (very tight book) → proportional.
        return b / b.sum()
    z = brentq(gap, lo, hi, xtol=tol)
    return _shin_probs(z, b)


def market_overround(odds: list[float]) -> float:
    return float(implied_probs(odds).sum() - 1.0)


if __name__ == "__main__":
    # Standard 1X2 example: typical bet365 odds with ~5% overround.
    cases = [
        ("Spain-CapeVerde 1X2", [1.25, 6.5, 13.0]),
        ("Even 1X2", [2.7, 3.3, 2.7]),
        ("O/U 2.5", [1.95, 1.95]),
        ("BTTS", [1.85, 1.95]),
    ]
    for label, odds in cases:
        prop = devig_proportional(odds)
        shin = devig_shin(odds)
        over = market_overround(odds)
        print(f"\n{label} · odds {odds} · overround {over*100:.2f}%")
        print(f"  proportional: {[round(p*100, 2) for p in prop]}")
        print(f"  shin       : {[round(p*100, 2) for p in shin]}")
        assert abs(prop.sum() - 1.0) < 1e-9
        assert abs(shin.sum() - 1.0) < 1e-6

    # Edge example: model vs de-vig probability
    print("\nedge example — Spain win @ odds 1.25:")
    p_devig = devig_shin([1.25, 6.5, 13.0])[0]
    p_model = 0.856  # from Spain-Cape Verde markets demo
    edge = p_model - p_devig
    print(f"  P(home) devig = {p_devig*100:.2f}%")
    print(f"  P(home) model = {p_model*100:.2f}%")
    print(f"  edge          = {edge*100:+.2f}%")
