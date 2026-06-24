"""Monte Carlo tournament simulation for WC2026.

Simulates 10k tournament runs using current group standings + Elo ratings.
Outputs probability of each team reaching each round (R16/QF/SF/F/Champion).

Stored in docs/data/projections.json — consumed by the Bracket tab in
the frontend.

Run: python -m tools.simulate_tournament
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRED = ROOT / "docs" / "data" / "predictions.json"
OUT = ROOT / "docs" / "data" / "projections.json"

N_SIMS = 10_000
HOME_ADV = 65.0  # Elo points


def _win_prob(rh: float, ra: float, neutral: bool = True) -> tuple[float, float, float]:
    """Return (p_home, p_draw, p_away) from Elo, with draw factor."""
    adv = 0.0 if neutral else HOME_ADV
    diff = (rh + adv) - ra
    p_home_no_draw = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    closeness = math.exp(-(diff ** 2) / (2 * 200 ** 2))
    p_draw = 0.28 * closeness
    p_home = p_home_no_draw * (1.0 - p_draw)
    p_away = (1.0 - p_home_no_draw) * (1.0 - p_draw)
    s = p_home + p_draw + p_away
    return p_home / s, p_draw / s, p_away / s


def _sim_match(rh: float, ra: float, ko: bool = False) -> str:
    """Returns 'h' (home wins) or 'a' (away wins). Draws resolved by penalty
    coin flip if ko=True; else 'd'."""
    ph, pd, pa = _win_prob(rh, ra, neutral=True)
    r = random.random()
    if r < ph:
        return "h"
    if r < ph + pd:
        if ko:
            # 50/50 penalty
            return "h" if random.random() < 0.5 else "a"
        return "d"
    return "a"


def simulate() -> dict:
    data = json.load(open(PRED))
    standings = data.get("standings", [])
    elo_top = {t["team"]: t["elo"] for t in data.get("elo_top", [])}

    if not standings:
        print("[sim] no standings; skipping")
        return {}

    # Default Elo if team not in top — use 1500
    def elo(name: str) -> float:
        return elo_top.get(name, 1500.0)

    # Counters per team
    reach_r16 = defaultdict(int)
    reach_qf = defaultdict(int)
    reach_sf = defaultdict(int)
    reach_f = defaultdict(int)
    win_tournament = defaultdict(int)
    all_teams: set[str] = set()

    for g in standings:
        for t in g.get("teams", []):
            all_teams.add(t["name"])

    print(f"[sim] running {N_SIMS} simulations over {len(all_teams)} teams in {len(standings)} groups")

    for _ in range(N_SIMS):
        # Build advancing teams using current standings status + Elo for ties
        # Use 'status' first (already-decided teams), then simulate remaining matchdays
        directs: list[str] = []   # 1st + 2nd per group
        thirds: list[tuple[str, float]] = []  # 3rd place candidates (team, pts proxy)

        for g in standings:
            teams = g.get("teams", [])
            # Use current pts as base; for unfinished, add expected gain from remaining games (Elo)
            base = []
            for t in teams:
                pts = t.get("pts", 0)
                played = t.get("played", 0)
                # Add stochastic gain for remaining (3 - played) matches
                remaining = 3 - played
                gain = 0
                for _ in range(remaining):
                    # Simulate vs an "average team in group" using mean Elo
                    others = [elo(o["name"]) for o in teams if o["name"] != t["name"]]
                    if not others:
                        continue
                    avg_elo = sum(others) / len(others)
                    res = _sim_match(elo(t["name"]), avg_elo, ko=False)
                    if res == "h":
                        gain += 3
                    elif res == "d":
                        gain += 1
                base.append({"name": t["name"], "pts": pts + gain, "elo": elo(t["name"])})

            # Rank by pts desc, tiebreak by Elo
            base.sort(key=lambda x: (-x["pts"], -x["elo"]))
            if len(base) >= 1:
                directs.append(base[0]["name"])
            if len(base) >= 2:
                directs.append(base[1]["name"])
            if len(base) >= 3:
                thirds.append((base[2]["name"], base[2]["pts"], base[2]["elo"]))

        # Best 8 of 12 thirds
        thirds.sort(key=lambda x: (-x[1], -x[2]))
        best_thirds = [t[0] for t in thirds[:8]]
        advancing = directs + best_thirds

        # Track R32 reach
        for team in advancing:
            reach_r16[team] += 1  # using r16 counter for "reached R32" since FIFA bracket trims to R16

        # Simulate KO rounds (random bracket pairings — bracket structure unknown
        # for WC2026 details; this gives unbiased advance probabilities)
        random.shuffle(advancing)

        # R32 → R16
        next_round = []
        for i in range(0, len(advancing) - 1, 2):
            res = _sim_match(elo(advancing[i]), elo(advancing[i + 1]), ko=True)
            winner = advancing[i] if res == "h" else advancing[i + 1]
            next_round.append(winner)
        for w in next_round:
            reach_qf[w] += 1

        # R16 → QF
        random.shuffle(next_round)
        next_round_2 = []
        for i in range(0, len(next_round) - 1, 2):
            res = _sim_match(elo(next_round[i]), elo(next_round[i + 1]), ko=True)
            winner = next_round[i] if res == "h" else next_round[i + 1]
            next_round_2.append(winner)
        for w in next_round_2:
            reach_sf[w] += 1

        # QF → SF
        random.shuffle(next_round_2)
        next_round_3 = []
        for i in range(0, len(next_round_2) - 1, 2):
            res = _sim_match(elo(next_round_2[i]), elo(next_round_2[i + 1]), ko=True)
            winner = next_round_2[i] if res == "h" else next_round_2[i + 1]
            next_round_3.append(winner)
        for w in next_round_3:
            reach_f[w] += 1

        # SF → F
        if len(next_round_3) >= 2:
            res = _sim_match(elo(next_round_3[0]), elo(next_round_3[1]), ko=True)
            champion = next_round_3[0] if res == "h" else next_round_3[1]
            win_tournament[champion] += 1

    # Build output
    out = []
    for team in all_teams:
        out.append({
            "team": team,
            "elo": round(elo(team), 0),
            "p_r32": round(reach_r16[team] / N_SIMS * 100, 1),
            "p_qf":  round(reach_qf[team] / N_SIMS * 100, 1),
            "p_sf":  round(reach_sf[team] / N_SIMS * 100, 1),
            "p_f":   round(reach_f[team] / N_SIMS * 100, 1),
            "p_win": round(win_tournament[team] / N_SIMS * 100, 2),
        })
    out.sort(key=lambda x: -x["p_win"])

    result = {
        "n_sims": N_SIMS,
        "teams": out,
        "generated_at": None,  # set by caller
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(OUT, "w"), indent=2)
    print(f"[sim] top 8 by championship probability:")
    for t in out[:8]:
        print(f"  {t['team']:25} win={t['p_win']:>5.2f}%  F={t['p_f']:>5.1f}%  SF={t['p_sf']:>5.1f}%")
    print(f"[sim] written: {OUT}")
    return result


if __name__ == "__main__":
    simulate()
