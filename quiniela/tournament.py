"""Tournament-aware pick optimisation.

Quiniela is not a betting market — it's a 16-player tournament with top-25%
prize cuts. Pure E[points] maximisation (always pick the mode) is the wrong
objective when:
  - Most opponents also pick the mode → everyone scores similar E[pts]
  - The winner is decided by VARIANCE: who hits 5-pt exacts
  - Tiebreaker by exact goal count rewards picks with more 1-pt bonuses

Three objective knobs:
  E[pts]            → expected points (Optim baseline)
  λ_var · SD[pts]   → variance reward (catch up / break ties)
  λ_tie · E[ties]   → tiebreaker bonus (E[goal-count exact matches])

Recommended λ values by situation:
  Leading by ≥ 5 pts: λ_var=0.0,  λ_tie=0.3  (preserve lead, win tiebreak)
  Even fight (±3 pts): λ_var=0.5, λ_tie=0.5  (balanced)
  Trailing by ≥ 5 pts: λ_var=0.9, λ_tie=0.4  (high-variance to catch up)
"""
from __future__ import annotations

import numpy as np

from quiniela.scoring import score_pick, tiebreak_goals


def risk_adjusted_pick(
    matrix: np.ndarray,
    lambda_var: float = 0.6,
    lambda_tie: float = 0.4,
) -> tuple[int, int, dict]:
    """Choose pick (h, a) maximising the risk-adjusted tournament objective.

    Objective:
        E[points] + λ_var · SD[points] + λ_tie · E[exact-goal hits]

    Where:
        - E[points] = expected GoPicks points for this pick under the
          model's joint distribution of (h_true, a_true).
        - SD[points] = standard deviation of GoPicks points.
        - E[exact-goal hits] = expected count of (home or away) exact goal
          matches across the joint distribution. Used to seek tiebreaker
          gains when two players have equal total points.

    Brute-forces over all (ph, pa) ∈ {0, …, n−1}². For n=11 this is 121
    candidates × 121 outcome cells = 14 641 cheap arithmetic ops. Fast.

    Parameters
    ----------
    matrix : ndarray
        Joint probability matrix from `model.markets.score_matrix`. Must
        sum to 1.
    lambda_var : float
        Reward for variance. Use 0 to recover the E[pts] optimum.
    lambda_tie : float
        Reward for tiebreaker (exact-goal hits).

    Returns
    -------
    (pick_h, pick_a, metrics)
        metrics is a dict with e_pts, sd, e_tie, objective and the top 5
        alternative picks ranked by objective.
    """
    n = matrix.shape[0]
    out: list[dict] = []
    for ph in range(n):
        for pa in range(n):
            e_pts = 0.0
            e_sq = 0.0
            e_tie = 0.0
            for h in range(n):
                for a in range(n):
                    p = float(matrix[h, a])
                    pts = score_pick(h, a, ph, pa)
                    ties = tiebreak_goals(h, a, ph, pa)
                    e_pts += p * pts
                    e_sq += p * pts * pts
                    e_tie += p * ties
            var = max(e_sq - e_pts * e_pts, 0.0)
            sd = var ** 0.5
            obj = e_pts + lambda_var * sd + lambda_tie * e_tie
            out.append({
                "ph": ph, "pa": pa,
                "e_pts": e_pts, "sd": sd, "e_tie": e_tie,
                "objective": obj,
            })
    out.sort(key=lambda r: r["objective"], reverse=True)
    top = out[0]
    return top["ph"], top["pa"], {**top, "alternatives": out[:5]}


def situation_presets(gap_to_top_pos: int) -> tuple[float, float, str]:
    """Suggest (lambda_var, lambda_tie, label) given gap to your target position.

    gap_to_top_pos > 0 means you are AHEAD of the target (defensive play).
    gap_to_top_pos = 0 tied at the boundary.
    gap_to_top_pos < 0 means you are BEHIND (need variance).
    """
    if gap_to_top_pos >= 5:
        return 0.0, 0.3, "leading-defensive"
    if gap_to_top_pos > 0:
        return 0.3, 0.4, "leading-balanced"
    if gap_to_top_pos == 0:
        return 0.5, 0.5, "tied-balanced"
    if gap_to_top_pos > -5:
        return 0.6, 0.4, "trailing-balanced"
    return 0.9, 0.4, "trailing-aggressive"
