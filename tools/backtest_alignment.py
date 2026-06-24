"""Backtest the align-bets-to-top fix on existing history.

For each historical bet, recompute whether it would still be a BET under
the new "favorite only per market" rule. Compare new ROI / win rate vs
the old (current) numbers.

Run: python -m tools.backtest_alignment
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "docs" / "data" / "history.json"


def _market_group(side: str, market: str) -> str:
    """Group key — picks within same group compete for BET."""
    s = (side or "").lower()
    m = (market or "").lower()
    if s in ("home", "draw", "away") or any(k in m for k in ("local", "visitante", "empate", "(1)", "(x)", "(2)")):
        return "1x2"
    if s.startswith("over") or s.startswith("under") or "over" in m or "under" in m:
        return "ou"
    if s.startswith("btts") or "btts" in m or "ambos marcan" in m:
        return "btts"
    if s.startswith("ah_") or "handicap" in m:
        return "ah"
    return "other"


def backtest() -> None:
    h = json.load(open(HIST))
    fixtures = h.get("fixtures", [])

    old_wins = 0
    old_losses = 0
    old_profit = 0.0
    new_wins = 0
    new_losses = 0
    new_profit = 0.0
    new_bets_kept = []
    new_bets_dropped = []

    for fx in fixtures:
        # Build all picks with model_prob — fall back to bets if picks missing
        picks_src = fx.get("picks", [])
        pick_by_side = {p.get("side"): p for p in picks_src if p.get("side")}

        for b in fx.get("bets", []):
            if b.get("won") is None:
                continue
            won = bool(b["won"])
            odds = float(b.get("odds", 1.0))

            # Old result
            old_wins += int(won)
            old_losses += int(not won)
            old_profit += (odds - 1) if won else -1

            # New: check if it was the model's top pick in its market group
            group = _market_group(b.get("side", ""), b.get("market", ""))

            # Find all picks in the same group for this fixture
            group_picks = []
            for s, p in pick_by_side.items():
                if _market_group(s, p.get("market", "")) == group:
                    mp = p.get("model_prob")
                    if mp is None:
                        continue
                    mp = mp / 100 if mp > 1 else mp
                    group_picks.append((s, mp))

            # Fall back to lookup via bets if picks not populated
            if not group_picks:
                for ob in fx.get("bets", []):
                    if _market_group(ob.get("side", ""), ob.get("market", "")) == group:
                        # We don't have model_prob for non-BET sides in bets[]
                        # so we can only judge if this is the only BET in the group
                        group_picks.append((ob.get("side", ""), 0.5))

            if not group_picks:
                # Can't decide — keep the original BET as new BET (conservative)
                new_wins += int(won)
                new_losses += int(not won)
                new_profit += (odds - 1) if won else -1
                new_bets_kept.append((fx.get("id"), b.get("market", ""), won, odds, "no-context"))
                continue

            # Top side by model prob
            top_side = max(group_picks, key=lambda x: x[1])[0]
            is_top = (b.get("side") == top_side)

            if is_top:
                new_wins += int(won)
                new_losses += int(not won)
                new_profit += (odds - 1) if won else -1
                new_bets_kept.append((fx.get("id"), b.get("market", ""), won, odds, "kept-top"))
            else:
                new_bets_dropped.append((fx.get("id"), b.get("market", ""), won, odds, top_side))

    old_n = old_wins + old_losses
    new_n = new_wins + new_losses

    print("=" * 60)
    print("BACKTEST: align-bets-to-top filter")
    print("=" * 60)
    print()
    print(f"{'metric':<25} {'OLD (current)':<18} {'NEW (aligned)':<18}")
    print("-" * 60)
    print(f"{'total bets':<25} {old_n:<18} {new_n:<18}")
    print(f"{'wins':<25} {old_wins:<18} {new_wins:<18}")
    print(f"{'losses':<25} {old_losses:<18} {new_losses:<18}")
    if old_n:
        print(f"{'win rate':<25} {old_wins/old_n*100:<17.1f}% {new_wins/new_n*100 if new_n else 0:<17.1f}%")
    print(f"{'profit (€1/bet)':<25} €{old_profit:<+17.2f} €{new_profit:<+17.2f}")
    if old_n:
        print(f"{'ROI':<25} {old_profit/old_n*100:<+17.1f}% {new_profit/new_n*100 if new_n else 0:<+17.1f}%")
    print()
    print(f"Bets dropped by alignment filter: {len(new_bets_dropped)}")
    if new_bets_dropped:
        won_drop = sum(1 for _, _, w, *_ in new_bets_dropped if w)
        print(f"  of which would have WON: {won_drop} ({won_drop/len(new_bets_dropped)*100:.1f}%)")
        print(f"  of which would have LOST: {len(new_bets_dropped)-won_drop}")
        avoided_profit = sum(((o - 1) if w else -1) for _, _, w, o, *_ in new_bets_dropped)
        print(f"  net profit avoided: €{avoided_profit:+.2f}")
    print()
    print("Sample dropped bets:")
    for fid, market, won, odds, reason in new_bets_dropped[:10]:
        print(f"  {fid:30} {market:25} @{odds:.2f} {'W' if won else 'L'} (top was {reason})")


if __name__ == "__main__":
    backtest()
