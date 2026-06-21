"""ROI gate. Walk-forward fit + simulate flat 1u bets where edge ≥ threshold.

Markets evaluated:
  - 1X2 home / draw / away   (devig: Shin on the 3-way)
  - AH home -1.5 / away +1.5 (devig: Shin on the 2-way, only when bookmaker line == ±1.5)

For each match in the aligned holdout:
  1. Fit DC on data strictly before the match's refit window
  2. Compute model probabilities
  3. Devig bookmaker odds → fair probability
  4. edge = p_model - p_devig
  5. If edge ≥ threshold → bet 1u at the bookmaker's decimal odds
  6. P/L per bet: win → odds-1, lose → -1
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from data.odds_match import load_aligned
from model.data_loader import load_matches
from model.devig import devig_shin
from model.dixon_coles import DCFit, fit
from model.markets import all_markets, score_matrix

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


@dataclass
class BetLog:
    date: pd.Timestamp
    market: str
    home: str
    away: str
    model_p: float
    devig_p: float
    edge: float
    odds: float
    won: bool
    pl: float


@dataclass
class MarketROI:
    name: str
    bets: list[BetLog] = field(default_factory=list)

    def add(self, b: BetLog) -> None:
        self.bets.append(b)

    def summary(self) -> dict:
        n = len(self.bets)
        if n == 0:
            return {"n": 0}
        stake = float(n)
        pnl = float(sum(b.pl for b in self.bets))
        wins = sum(1 for b in self.bets if b.won)
        roi = pnl / stake
        winr = wins / n
        # Drawdown
        equity = np.cumsum([b.pl for b in self.bets])
        peak = np.maximum.accumulate(equity)
        dd = float(np.min(equity - peak))
        avg_odds = float(np.mean([b.odds for b in self.bets]))
        avg_edge = float(np.mean([b.edge for b in self.bets]))
        # Sharpe over per-bet P/L
        pls = np.array([b.pl for b in self.bets])
        sharpe = float(pls.mean() / pls.std() * np.sqrt(n)) if pls.std() > 0 else 0.0
        return {
            "n": n,
            "stake": stake,
            "pnl": pnl,
            "roi": roi,
            "win_rate": winr,
            "max_drawdown": dd,
            "avg_odds": avg_odds,
            "avg_edge": avg_edge,
            "sharpe": sharpe,
        }


def _refit_dates(holdout_dates: pd.Series, cadence_days: int) -> list[pd.Timestamp]:
    start = holdout_dates.min().normalize()
    end = holdout_dates.max().normalize()
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur)
        cur = cur + pd.Timedelta(days=cadence_days)
    return dates


def run(
    edge_threshold: float = 0.07,
    cadence_days: int = 60,
    bookmaker: str | None = None,
    output_json: Path | None = None,
) -> dict:
    aligned = load_aligned()
    if bookmaker:
        aligned = aligned[aligned["bookmaker"] == bookmaker]
    aligned = aligned.sort_values("date").reset_index(drop=True)
    print(f"aligned events: {len(aligned)}{' (bookmaker=' + bookmaker + ')' if bookmaker else ''}")

    all_df = load_matches()
    refit_dates = _refit_dates(aligned["date"], cadence_days)
    print(f"refit dates: {len(refit_dates)} · cadence {cadence_days}d · threshold {edge_threshold*100:.1f}%")

    markets = {
        "1x2_home": MarketROI("1X2 home"),
        "1x2_draw": MarketROI("1X2 draw"),
        "1x2_away": MarketROI("1X2 away"),
        "ah_home_-1.5": MarketROI("AH home -1.5"),
        "ah_away_+1.5": MarketROI("AH away +1.5"),
    }

    DCFit.__module__ = "model.dixon_coles"
    n_predicted = 0
    n_skipped = 0

    for i, refit_date in enumerate(refit_dates):
        window_end = (
            refit_dates[i + 1] if i + 1 < len(refit_dates)
            else aligned["date"].max() + pd.Timedelta(days=1)
        )
        # Walk-forward: refit using as_of = refit_date so time decay is honest.
        as_of_train = load_matches(as_of=refit_date)
        as_of_train = as_of_train[as_of_train["date"] < refit_date]
        if len(as_of_train) < 500:
            continue

        print(f"\n[{i+1}/{len(refit_dates)}] refit {refit_date.date()} · train={len(as_of_train)}")
        f = fit(as_of_train, verbose=False)

        window = aligned[(aligned["date"] >= refit_date) & (aligned["date"] < window_end)]
        print(f"  evaluating {len(window)} aligned events")

        for _, m in window.iterrows():
            h, a = m["home_team"], m["away_team"]
            if h not in f.alpha or a not in f.alpha:
                n_skipped += 1
                continue
            lh, la = f.expected_goals(h, a, neutral=bool(m["neutral"]))
            mat = score_matrix(lh, la, f.rho)
            mp = all_markets(mat)

            hs, as_ = m["home_score"], m["away_score"]
            diff = hs - as_

            # ─── 1X2 ───
            if pd.notna(m["h2h_home"]) and pd.notna(m["h2h_draw"]) and pd.notna(m["h2h_away"]):
                try:
                    devig = devig_shin([m["h2h_home"], m["h2h_draw"], m["h2h_away"]])
                except Exception:
                    devig = None
                if devig is not None:
                    actual_home = int(diff > 0)
                    actual_draw = int(diff == 0)
                    actual_away = int(diff < 0)

                    for key, side, model_p, devig_p, odds, actual in [
                        ("1x2_home", "home", mp["1x2"]["home"], float(devig[0]), m["h2h_home"], actual_home),
                        ("1x2_draw", "draw", mp["1x2"]["draw"], float(devig[1]), m["h2h_draw"], actual_draw),
                        ("1x2_away", "away", mp["1x2"]["away"], float(devig[2]), m["h2h_away"], actual_away),
                    ]:
                        edge = model_p - devig_p
                        if edge >= edge_threshold:
                            pl = (odds - 1) if actual == 1 else -1.0
                            markets[key].add(BetLog(
                                date=m["date"], market=side, home=h, away=a,
                                model_p=float(model_p), devig_p=float(devig_p),
                                edge=float(edge), odds=float(odds),
                                won=bool(actual), pl=float(pl),
                            ))

            # ─── AH ±1.5 ───
            if pd.notna(m["spread_home_handicap"]) and abs(m["spread_home_handicap"]) == 1.5:
                if pd.notna(m["spread_home_price"]) and pd.notna(m["spread_away_price"]):
                    try:
                        devig_ah = devig_shin([m["spread_home_price"], m["spread_away_price"]])
                    except Exception:
                        devig_ah = None
                    if devig_ah is not None:
                        # If home handicap is -1.5: home covers iff diff >= 2
                        # If home handicap is +1.5: home covers iff diff >= -1
                        line = float(m["spread_home_handicap"])
                        home_cov_actual = int((diff + line) > 0)
                        away_cov_actual = int((diff + line) < 0)

                        if line == -1.5:
                            model_p_home = mp["ah_home_-1_5"]["home_cover"]
                            model_p_away = mp["ah_home_-1_5"]["away_cover"]
                            key_home, key_away = "ah_home_-1.5", "ah_away_+1.5"
                        else:  # line == +1.5
                            model_p_home = mp["ah_home_+1_5"]["home_cover"]
                            model_p_away = mp["ah_home_+1_5"]["away_cover"]
                            key_home, key_away = "ah_away_+1.5", "ah_home_-1.5"

                        for key, model_p, devig_p, odds, actual in [
                            (key_home, model_p_home, float(devig_ah[0]),
                             m["spread_home_price"], home_cov_actual),
                            (key_away, model_p_away, float(devig_ah[1]),
                             m["spread_away_price"], away_cov_actual),
                        ]:
                            edge = model_p - devig_p
                            if edge >= edge_threshold:
                                pl = (odds - 1) if actual == 1 else -1.0
                                markets[key].add(BetLog(
                                    date=m["date"], market=key, home=h, away=a,
                                    model_p=float(model_p), devig_p=float(devig_p),
                                    edge=float(edge), odds=float(odds),
                                    won=bool(actual), pl=float(pl),
                                ))

            n_predicted += 1

    print(f"\nmatches predicted: {n_predicted}")
    print(f"skipped (team not in train): {n_skipped}")

    summary = {
        "config": {
            "edge_threshold": edge_threshold,
            "cadence_days": cadence_days,
        },
        "n_predicted": n_predicted,
        "n_skipped": n_skipped,
        "markets": {k: m.summary() for k, m in markets.items()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    print("\n" + "=" * 100)
    print(f"{'Market':18} {'N':>4} {'P/L':>8} {'ROI':>8} {'WinR':>7} {'AvgOdds':>9} {'AvgEdge':>9} {'MaxDD':>9} {'Sharpe':>8}")
    print("-" * 100)
    for k, m in markets.items():
        s = m.summary()
        if s.get("n", 0) == 0:
            print(f"{k:18} {0:>4} {'—':>8} {'—':>8} {'—':>7} {'—':>9} {'—':>9} {'—':>9} {'—':>8}")
            continue
        print(
            f"{k:18} {s['n']:>4} "
            f"{s['pnl']:>+8.2f} "
            f"{s['roi']*100:>+7.2f}% "
            f"{s['win_rate']*100:>6.1f}% "
            f"{s['avg_odds']:>9.2f} "
            f"{s['avg_edge']*100:>+8.2f}% "
            f"{s['max_drawdown']:>+9.2f} "
            f"{s['sharpe']:>+8.2f}"
        )
    print("=" * 100)

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, "w") as fp:
            json.dump(summary, fp, indent=2, default=float)
        print(f"\nreport saved → {output_json}")

        # Dump full bet log for offline validation (bootstrap, temporal split, BH).
        bet_log_path = output_json.with_suffix(".bets.csv")
        rows = []
        for k, m in markets.items():
            for b in m.bets:
                rows.append({
                    "market": k,
                    "date": b.date.isoformat() if hasattr(b.date, "isoformat") else str(b.date),
                    "home": b.home,
                    "away": b.away,
                    "model_p": b.model_p,
                    "devig_p": b.devig_p,
                    "edge": b.edge,
                    "odds": b.odds,
                    "won": int(b.won),
                    "pl": b.pl,
                })
        if rows:
            pd.DataFrame(rows).to_csv(bet_log_path, index=False)
            print(f"bet log saved → {bet_log_path}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.07)
    parser.add_argument("--cadence", type=int, default=60)
    parser.add_argument("--bookmaker", type=str, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    suffix = f"_t{int(args.threshold*100)}"
    if args.bookmaker:
        suffix += f"_{args.bookmaker}"
    out_path = args.out or REPORTS_DIR / f"roi_{datetime.now().strftime('%Y%m%d_%H%M')}{suffix}.json"
    run(
        edge_threshold=args.threshold,
        cadence_days=args.cadence,
        bookmaker=args.bookmaker,
        output_json=out_path,
    )
