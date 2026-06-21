"""GoPicks WC2026 quiniela scoring.

Official rules (additive):
  +3 pts → correct winner (1 / X / 2)
  +1 pt  → home goals exact
  +1 pt  → away goals exact
  → max 5 pts (when winner correct AND both goal counts exact).

Tiebreaker between equal-points entries: total exact goal predictions
(sum of all the +1 bonuses across matches).
"""
from __future__ import annotations


def winner(h: int, a: int) -> str:
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def score_pick(actual_h: int, actual_a: int, pick_h: int, pick_a: int) -> int:
    """+3 if winner direction correct, +1 per exact goal count."""
    pts = 0
    if winner(actual_h, actual_a) == winner(pick_h, pick_a):
        pts += 3
    if actual_h == pick_h:
        pts += 1
    if actual_a == pick_a:
        pts += 1
    return pts


def tiebreak_goals(actual_h: int, actual_a: int, pick_h: int, pick_a: int) -> int:
    """Number of exact goal-count matches (0, 1 or 2). Used for tiebreaker."""
    return int(actual_h == pick_h) + int(actual_a == pick_a)


if __name__ == "__main__":
    # Verify rules with known results
    cases = [
        # (actual_h, actual_a, pick_h, pick_a, expected_pts, label)
        (0, 1, 0, 1, 5, "Haiti 0-1 Scotland exact"),
        (1, 1, 1, 1, 5, "Brazil 1-1 Morocco exact"),
        (1, 0, 2, 1, 3, "Mexico 1-0 Korea: winner only"),
        (6, 0, 2, 0, 4, "Canada 6-0 Qatar: winner + away 0 exact"),
        (3, 0, 2, 0, 4, "Argentina 3-0 Algeria: winner + away 0"),
        (3, 1, 2, 1, 4, "France 3-1 Senegal: winner + away 1 exact"),
        (1, 1, 1, 0, 1, "Czechia 1-1: wrong winner, home 1 exact"),
        (1, 0, 1, 1, 1, "Ghana 1-0: wrong winner, home 1 exact"),
        (4, 2, 1, 1, 0, "England 4-2: wrong winner, no goal match"),
        (2, 2, 1, 1, 3, "NED 2-2 pick 1-1: draw correct, no exact"),
        (5, 1, 1, 0, 3, "Sweden 5-1: winner only"),
        (1, 1, 0, 2, 0, "Qatar 1-1: wrong winner, no goal match"),
    ]
    all_ok = True
    for a_h, a_a, p_h, p_a, exp, label in cases:
        got = score_pick(a_h, a_a, p_h, p_a)
        ok = got == exp
        if not ok:
            all_ok = False
        flag = "✓" if ok else "✗"
        print(f"  {flag} {label}: got={got} expected={exp}")
    print("\nALL OK" if all_ok else "\nFAILURES")
