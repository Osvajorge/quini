"""Threshold tuner: ROI per edge bucket from history.json.

Reports the actual ROI you would have achieved at each edge threshold,
so the cutoff isn't industry-convention (7%) — it's evidence-based.

Run: python -m tools.tune_threshold
Optional: python -m tools.tune_threshold --min-prob 0.20 --ev-min 0.03
"""
from __future__ import annotations
import argparse
import json
from collections import defaultdict
from pathlib import Path

HIST = Path(__file__).resolve().parent.parent / "docs" / "data" / "history.json"

EDGE_BUCKETS = [
    (-100, 3),
    (3, 5),
    (5, 7),
    (7, 9),
    (9, 12),
    (12, 16),
    (16, 100),
]


def _market_category(market: str) -> str:
    """Group raw market labels into broad categories."""
    m = (market or "").lower()
    if any(k in m for k in ("local", "visitante", "empate", "(1)", "(x)", "(2)", "home", "away", "draw")):
        return "1X2"
    if "over" in m or "under" in m or "o/u" in m:
        return "O/U"
    if "btts" in m or "ambos marcan" in m or "both score" in m:
        return "BTTS"
    if "ah" in m or "handicap" in m or "asiático" in m:
        return "AH"
    return "Other"


def _bucket(edge: float) -> str:
    for lo, hi in EDGE_BUCKETS:
        if lo <= edge < hi:
            if lo == -100:
                return f"<{hi}"
            if hi == 100:
                return f"{lo}+"
            return f"{lo}-{hi}"
    return "?"


def tune(min_prob: float, ev_min: float) -> None:
    h = json.load(open(HIST))
    fixtures = h.get("fixtures", [])

    # Flatten all picks across all completed fixtures
    # Source 1: picks[] (new format, has all SKIPs too — once cron runs)
    # Source 2: bets[] (legacy, only BETs but has `won` field)
    all_picks = []
    for fx in fixtures:
        # Prefer picks[] if any has `won` field set
        picks_src = fx.get("picks", [])
        has_won_in_picks = any(p.get("won") is not None for p in picks_src)
        if has_won_in_picks:
            for p in picks_src:
                if p.get("won") is None:
                    continue
                mp = p.get("model_prob")
                odds = p.get("odds")
                edge = p.get("edge")
                if mp is None or odds is None or edge is None:
                    continue
                all_picks.append({
                    "edge": float(edge),
                    "model_prob": float(mp) / 100 if mp > 1 else float(mp),
                    "odds": float(odds),
                    "won": bool(p["won"]),
                    "fid": fx.get("id"),
                    "market": p.get("market"),
                })
        else:
            # Fallback: bets[] (only BETs, no model_prob)
            for b in fx.get("bets", []):
                if b.get("won") is None:
                    continue
                # find matching pick to get model_prob
                mp = None
                for p in picks_src:
                    if p.get("side") == b.get("side") or p.get("market") == b.get("market"):
                        mp = p.get("model_prob")
                        break
                odds = b.get("odds")
                edge = b.get("edge")
                if odds is None or edge is None:
                    continue
                all_picks.append({
                    "edge": float(edge),
                    "model_prob": float(mp) / 100 if mp and mp > 1 else (float(mp) if mp else 0.5),
                    "odds": float(odds),
                    "won": bool(b["won"]),
                    "fid": fx.get("id"),
                    "market": b.get("market"),
                })

    if not all_picks:
        print("No picks with outcomes — history empty or missing won field.")
        print("This is normal until the next cron run after this commit lands.")
        return

    print(f"Total picks with outcomes: {len(all_picks)}")
    print(f"Date range: from {min(fx.get('date','')[:10] for fx in fixtures if fx.get('date'))} "
          f"to {max(fx.get('date','')[:10] for fx in fixtures if fx.get('date'))}")
    print()

    # === Group by edge bucket ===
    by_bucket = defaultdict(list)
    for p in all_picks:
        by_bucket[_bucket(p["edge"])].append(p)

    print("ROI by edge bucket (no other filters):")
    print(f"{'bucket':<10} {'n':>4} {'wins':>5} {'win%':>6} {'avg odds':>9} {'profit':>8} {'ROI':>7}")
    for lo, hi in EDGE_BUCKETS:
        key = f"<{hi}" if lo == -100 else f"{lo}+" if hi == 100 else f"{lo}-{hi}"
        picks = by_bucket.get(key, [])
        if not picks:
            continue
        wins = sum(1 for p in picks if p["won"])
        # Profit assuming €1 stake per bet
        profit = sum((p["odds"] - 1) if p["won"] else -1 for p in picks)
        roi = profit / len(picks) * 100
        avg_odds = sum(p["odds"] for p in picks) / len(picks)
        print(f"{key:<10} {len(picks):>4} {wins:>5} {wins/len(picks)*100:>5.1f}% "
              f"{avg_odds:>8.2f}  €{profit:>+6.2f}  {roi:>+5.1f}%")
    print()

    # === Simulate different edge thresholds ===
    print(f"ROI at different edge thresholds (with min_prob={min_prob:.2f}, ev_min={ev_min:.2f}):")
    print(f"{'edge ≥':<8} {'n bets':>7} {'win%':>6} {'profit':>8} {'ROI':>7} {'Sharpe-like':>11}")
    for thr in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15]:
        thr_pct = thr * 100
        sel = [
            p for p in all_picks
            if p["edge"] >= thr_pct
            and p["model_prob"] >= min_prob
            and (p["model_prob"] * p["odds"] - 1) >= ev_min
        ]
        if not sel:
            print(f"{thr_pct:>6.0f}%  {'—':>7}")
            continue
        wins = sum(1 for p in sel if p["won"])
        profit = sum((p["odds"] - 1) if p["won"] else -1 for p in sel)
        roi = profit / len(sel) * 100
        # rough Sharpe: ROI / stdev of per-bet returns
        rets = [(p["odds"] - 1) if p["won"] else -1 for p in sel]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        sd = var ** 0.5
        sharpe = mean / sd if sd > 0 else 0
        print(f"{thr_pct:>6.0f}%  {len(sel):>7} {wins/len(sel)*100:>5.1f}% "
              f"€{profit:>+6.2f}  {roi:>+5.1f}%  {sharpe:>+10.3f}")
    print()

    # === Min model_prob sweep at current edge=7% ===
    print(f"ROI at different MIN_MODEL_PROB (edge ≥ 7%, ev_min={ev_min:.2f}):")
    print(f"{'min p':<7} {'n bets':>7} {'win%':>6} {'profit':>8} {'ROI':>7}")
    for mp in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        sel = [
            p for p in all_picks
            if p["edge"] >= 7.0
            and p["model_prob"] >= mp
            and (p["model_prob"] * p["odds"] - 1) >= ev_min
        ]
        if not sel:
            print(f"{mp:.2f}    {'—':>7}")
            continue
        wins = sum(1 for p in sel if p["won"])
        profit = sum((p["odds"] - 1) if p["won"] else -1 for p in sel)
        roi = profit / len(sel) * 100
        print(f"{mp:.2f}    {len(sel):>7} {wins/len(sel)*100:>5.1f}% €{profit:>+6.2f}  {roi:>+5.1f}%")
    print()

    # === Per-market breakdown ===
    print("ROI by market × edge threshold (min_prob=%.2f, ev_min=%.2f):" % (min_prob, ev_min))
    by_market = defaultdict(list)
    for p in all_picks:
        cat = _market_category(p["market"])
        by_market[cat].append(p)
    for cat in sorted(by_market.keys()):
        picks_m = by_market[cat]
        print(f"\n  [{cat}]  n={len(picks_m)}")
        print(f"  {'edge ≥':<8} {'n':>5} {'win%':>6} {'profit':>8} {'ROI':>7}")
        for thr in [0.05, 0.07, 0.08, 0.10, 0.12]:
            thr_pct = thr * 100
            sel = [
                p for p in picks_m
                if p["edge"] >= thr_pct
                and p["model_prob"] >= min_prob
                and (p["model_prob"] * p["odds"] - 1) >= ev_min
            ]
            if not sel:
                print(f"  {thr_pct:>6.0f}%  {'—':>5}")
                continue
            wins = sum(1 for p in sel if p["won"])
            profit = sum((p["odds"] - 1) if p["won"] else -1 for p in sel)
            roi = profit / len(sel) * 100
            print(f"  {thr_pct:>6.0f}%  {len(sel):>5} {wins/len(sel)*100:>5.1f}% €{profit:>+6.2f}  {roi:>+5.1f}%")
    print()

    # === Recommendation ===
    best_thr, best_roi, best_n = None, -999, 0
    for thr in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12]:
        thr_pct = thr * 100
        sel = [
            p for p in all_picks
            if p["edge"] >= thr_pct
            and p["model_prob"] >= min_prob
            and (p["model_prob"] * p["odds"] - 1) >= ev_min
        ]
        if len(sel) < 10:  # require min sample
            continue
        profit = sum((p["odds"] - 1) if p["won"] else -1 for p in sel)
        roi = profit / len(sel) * 100
        if roi > best_roi:
            best_thr, best_roi, best_n = thr_pct, roi, len(sel)

    if best_thr is not None:
        print(f"→ Recommended EDGE_THRESHOLD: {best_thr/100:.2f}  "
              f"(ROI {best_roi:+.1f}% over {best_n} bets)")
        print(f"  Edit model/predict.py:12  EDGE_THRESHOLD = {best_thr/100:.2f}")
    else:
        print("→ Not enough sample size at any threshold (need 10+ bets per bucket).")
        print("  Keep current 7% until history grows.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-prob", type=float, default=0.25,
                    help="Min model probability (default 0.25)")
    ap.add_argument("--ev-min", type=float, default=0.04,
                    help="Min expected value (default 0.04)")
    args = ap.parse_args()
    tune(args.min_prob, args.ev_min)
