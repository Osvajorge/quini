"""Monte Carlo tournament simulation for WC2026 — conditioned on results.

The group stage and any completed knockout games are treated as ground truth:
losers of finished KO fixtures are eliminated (probability 0 from that round on),
winners are locked into the next round, and only the *remaining* fixtures are
Monte-Carlo'd. This mirrors github.com/Hicruben/world-cup-2026-prediction-model
("finished matches lock in actual results; simulation runs only remaining
fixtures").

Outputs probability of each team reaching each stage (R32/QF/SF/F/Champion).
Stored in docs/data/projections.json — consumed by the Bracket tab in the
frontend.

Run: python -m tools.simulate_tournament
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRED = ROOT / "docs" / "data" / "predictions.json"
OUT = ROOT / "docs" / "data" / "projections.json"

N_SIMS = 10_000

# Odds-API fixture names → ESPN standings display names (only where they differ).
# Kept in sync with api/generate.py::_FIXTURE_TO_STANDING so completed KO fixtures
# resolve against the same team-name space used by standings / Elo.
_FIXTURE_TO_STANDING: dict[str, str] = {
    "USA": "United States",
    "Bosnia & Herzegovina": "Bosnia-Herzegovina",
    "Bosnia and Herzegovina": "Bosnia-Herzegovina",
    "DR Congo": "Congo DR",
    "Turkey": "Türkiye",
    "Czech Republic": "Czechia",
}


def _std(name: str | None) -> str | None:
    """Map a fixture team name into the standings/Elo name space."""
    if name is None:
        return None
    return _FIXTURE_TO_STANDING.get(name, name)


def _win_prob(rh: float, ra: float, neutral: bool = True) -> tuple[float, float, float]:
    """Return (p_home, p_draw, p_away) from Elo, with draw factor."""
    HOME_ADV = 65.0
    adv = 0.0 if neutral else HOME_ADV
    diff = (rh + adv) - ra
    p_home_no_draw = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    closeness = math.exp(-(diff ** 2) / (2 * 200 ** 2))
    p_draw = 0.28 * closeness
    p_home = p_home_no_draw * (1.0 - p_draw)
    p_away = (1.0 - p_home_no_draw) * (1.0 - p_draw)
    s = p_home + p_draw + p_away
    return p_home / s, p_draw / s, p_away / s


def _ko_winner(a: str, b: str, elo) -> str:
    """Simulate one knockout tie between a and b; draws → 50/50 penalties."""
    ph, pd, _ = _win_prob(elo(a), elo(b), neutral=True)
    r = random.random()
    if r < ph:
        return a
    if r < ph + pd:
        # extra time modelled as coin-flip penalties
        return a if random.random() < 0.5 else b
    return b


def _build_field(standings: list, elo) -> list[str]:
    """Deterministic KO field from final group standings: top 2 of every group
    plus the 8 best third-placed teams (pts, Elo tiebreak). The group stage is
    complete once knockouts are under way, so this is a fixed set."""
    directs: list[str] = []
    thirds: list[tuple[str, float, float]] = []
    for g in standings:
        ranked = sorted(
            g.get("teams", []),
            key=lambda t: (-t.get("pts", 0), -elo(t["name"])),
        )
        for t in ranked[:2]:
            directs.append(t["name"])
        if len(ranked) >= 3:
            third = ranked[2]
            thirds.append((third["name"], third.get("pts", 0), elo(third["name"])))
    thirds.sort(key=lambda x: (-x[1], -x[2]))
    best_thirds = [t[0] for t in thirds[:8]]
    return directs + best_thirds


def _resolve_ko_results(fixtures: list) -> tuple[dict[str, int], set[str], dict[int, list[tuple[str, str]]]]:
    """Read completed / pending knockout fixtures and return:
      - locked_wins: {team -> number of KO ties already won}  (winners of finished ties)
      - eliminated:  teams that lost a completed KO tie (respecting penalty_winner)
      - pending:     {round_depth -> [(a, b), ...]} remaining fixtures to simulate,
                     bucketed by the round they belong to (depth = KO wins each
                     participant carries into the tie).
    """
    ko = [f for f in fixtures if f.get("is_ko")]
    locked_wins: dict[str, int] = defaultdict(int)
    eliminated: set[str] = set()
    pending_raw: list[tuple[str, str]] = []

    for f in ko:
        home, away = _std(f.get("home")), _std(f.get("away"))
        if not home or not away:
            continue
        if f.get("completed"):
            pen = _std(f.get("penalty_winner"))
            ah, aa = f.get("actual_home"), f.get("actual_away")
            if pen in (home, away):
                winner = pen
            elif ah is not None and aa is not None and ah != aa:
                winner = home if ah > aa else away
            else:
                # completed but no resolvable winner — skip (shouldn't happen in KO)
                continue
            loser = away if winner == home else home
            locked_wins[winner] += 1
            eliminated.add(loser)
        else:
            pending_raw.append((home, away))

    # A pending tie belongs to the round equal to the KO wins its participants
    # already carry (both sides should match: e.g. two R32 winners meet in R16).
    pending: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for a, b in pending_raw:
        depth = min(locked_wins.get(a, 0), locked_wins.get(b, 0))
        pending[depth].append((a, b))

    return dict(locked_wins), eliminated, dict(pending)


def simulate() -> dict:
    data = json.load(open(PRED))
    standings = data.get("standings", [])
    fixtures = data.get("fixtures", [])
    elo_top = {t["team"]: t["elo"] for t in data.get("elo_top", [])}

    if not standings:
        print("[sim] no standings; skipping")
        return {}

    def elo(name: str) -> float:
        return elo_top.get(name, 1500.0)

    # ------------------------------------------------------------------ setup
    field = _build_field(standings, elo)

    locked_wins, eliminated, pending = _resolve_ko_results(fixtures)

    # Every team already in a KO fixture has certainly advanced — make sure the
    # field contains them even if the standings-derived thirds disagree.
    ko_participants = [t for pair in _resolve_ko_participants(fixtures) for t in pair]
    for t in ko_participants:
        if t not in field:
            field.append(t)

    all_teams: set[str] = {t["name"] for g in standings for t in g.get("teams", [])}
    all_teams.update(field)

    # 5 knockout rounds: R32 -> R16 -> QF -> SF -> F. A team's running win count
    # marks the deepest stage it has reached:
    #   >=2 wins -> reached QF, >=3 -> SF, >=4 -> Final, ==5 -> Champion.
    N_ROUNDS = 5

    reach_r32 = defaultdict(int)
    reach_qf = defaultdict(int)
    reach_sf = defaultdict(int)
    reach_f = defaultdict(int)
    win_tournament = defaultdict(int)

    n_locked = sum(1 for f in fixtures if f.get("is_ko") and f.get("completed"))
    n_pending = sum(len(v) for v in pending.values())
    print(
        f"[sim] running {N_SIMS} sims | field={len(field)} teams | "
        f"{n_locked} KO ties locked ({len(eliminated)} eliminated) | "
        f"{n_pending} KO ties remaining"
    )

    for _ in range(N_SIMS):
        # Every field team has reached the round of 32 (a settled fact).
        for t in field:
            reach_r32[t] += 1

        # Alive teams carry the KO wins already banked from completed ties.
        alive: dict[str, int] = {
            t: locked_wins.get(t, 0) for t in field if t not in eliminated
        }

        for r in range(N_ROUNDS):
            pool = [t for t, w in alive.items() if w == r]
            if not pool:
                continue
            winners: list[str] = []
            used: set[str] = set()

            # Respect real remaining fixtures for this round where we have them.
            for a, b in pending.get(r, []):
                if a in alive and b in alive and a not in used and b not in used:
                    winners.append(_ko_winner(a, b, elo))
                    used.add(a)
                    used.add(b)

            # Randomly pair whatever the bracket doesn't yet pin down.
            rest = [t for t in pool if t not in used]
            random.shuffle(rest)
            for i in range(0, len(rest) - 1, 2):
                winners.append(_ko_winner(rest[i], rest[i + 1], elo))
                used.add(rest[i])
                used.add(rest[i + 1])
            if len(rest) % 2 == 1:  # odd team out gets a bye
                winners.append(rest[-1])

            for w in winners:
                alive[w] = r + 1

        # Tally deepest stage reached from final win counts.
        for t, w in alive.items():
            if w >= 2:
                reach_qf[t] += 1
            if w >= 3:
                reach_sf[t] += 1
            if w >= 4:
                reach_f[t] += 1
            if w >= N_ROUNDS:
                win_tournament[t] += 1

    # ----------------------------------------------------------------- output
    out = []
    for team in all_teams:
        out.append({
            "team": team,
            "elo": round(elo(team), 0),
            "p_r32": round(reach_r32[team] / N_SIMS * 100, 1),
            "p_qf":  round(reach_qf[team] / N_SIMS * 100, 1),
            "p_sf":  round(reach_sf[team] / N_SIMS * 100, 1),
            "p_f":   round(reach_f[team] / N_SIMS * 100, 1),
            "p_win": round(win_tournament[team] / N_SIMS * 100, 2),
        })
    out.sort(key=lambda x: -x["p_win"])

    result = {
        "n_sims": N_SIMS,
        "teams": out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(OUT, "w"), indent=2)

    total_win = sum(t["p_win"] for t in out)
    print(f"[sim] survivors' championship prob sums to {total_win:.1f}%")
    print(f"[sim] top 8 by championship probability:")
    for t in out[:8]:
        print(f"  {t['team']:25} win={t['p_win']:>5.2f}%  F={t['p_f']:>5.1f}%  SF={t['p_sf']:>5.1f}%")
    print(f"[sim] written: {OUT}")
    return result


def _resolve_ko_participants(fixtures: list) -> list[tuple[str, str]]:
    """All (home, away) pairs of KO fixtures, in standings name space."""
    pairs = []
    for f in fixtures:
        if f.get("is_ko"):
            h, a = _std(f.get("home")), _std(f.get("away"))
            if h and a:
                pairs.append((h, a))
    return pairs


if __name__ == "__main__":
    simulate()
