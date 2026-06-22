"""Bivariate Poisson goal model via penaltyblog (Karlis-Ntzoufras).

Replaces Dixon-Coles as the core prediction model. Advantages:
- Models correlation between home/away goals explicitly (λ3 shared goals)
- Faster convergence (~70s vs ~270s on Apple Silicon)
- Better log-likelihood on same data
- FootballProbabilityGrid gives 1X2, BTTS, O/U, AH, exact score natively
"""
from __future__ import annotations

import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from penaltyblog.models import BivariatePoissonGoalModel

from model.data_loader import load_matches, team_match_counts

FIT_PATH = Path(__file__).resolve().parent.parent / "data" / "fit.pkl"


@dataclass
class BPFit:
    model: BivariatePoissonGoalModel
    teams: list[str]
    fitted_at: datetime
    n_matches: int
    log_likelihood: float
    converged: bool
    n_iter: int
    match_counts: dict[str, int] = field(default_factory=dict)

    def predict_grid(self, home: str, away: str, neutral: bool = False):
        """Return FootballProbabilityGrid for the given match-up."""
        if home not in self.teams or away not in self.teams:
            raise KeyError(f"Unknown team(s): {home!r}, {away!r}")
        return self.model.predict(home, away, neutral_venue=neutral)

    def expected_goals(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        grid = self.predict_grid(home, away, neutral)
        dist_h = grid.home_goal_distribution()
        dist_a = grid.away_goal_distribution()
        k = np.arange(len(dist_h))
        return float(np.sum(k * dist_h)), float(np.sum(k * dist_a))

    def score_matrix(self, home: str, away: str, neutral: bool = False, n: int = 10) -> np.ndarray:
        """Joint (home, away) score probability matrix, shape (n, n), normalized."""
        grid = self.predict_grid(home, away, neutral)
        mat = np.array([[grid.exact_score(h, a) for a in range(n)] for h in range(n)])
        s = mat.sum()
        return mat / s if s > 0 else mat

    def to_dict(self) -> dict:
        params = self.model.get_params()
        return {
            "model": "BivariatePoissonGoalModel",
            "teams": len(self.teams),
            "fitted_at": self.fitted_at.isoformat(),
            "n_matches": self.n_matches,
            "log_likelihood": self.log_likelihood,
            "converged": self.converged,
            "n_iter": self.n_iter,
            "home_advantage": params.get("home_advantage"),
            "correlation_log": params.get("correlation_log"),
        }


def fit(
    df: pd.DataFrame | None = None,
    verbose: bool = True,
) -> BPFit:
    if df is None:
        df = load_matches()

    teams = sorted(set(df["home_team"]) | set(df["away_team"]))

    t0 = time.perf_counter()
    model = BivariatePoissonGoalModel(
        goals_home=df["home_score"].values.copy(),
        goals_away=df["away_score"].values.copy(),
        teams_home=df["home_team"].values.copy(),
        teams_away=df["away_team"].values.copy(),
        weights=df["weight"].values.copy(),
        neutral_venue=df["neutral"].astype(int).values.copy(),
    )
    model.fit()
    elapsed = time.perf_counter() - t0

    res = model._res
    log_l = float(-res.fun)

    if verbose:
        params = model.get_params()
        ha = params.get("home_advantage", float("nan"))
        print(f"fit done in {elapsed:.1f}s · iters={res.nit} · converged={res.success}")
        print(f"  home_adv={ha:.4f} · log-L={log_l:.1f}")

    counts = team_match_counts(df).to_dict()

    return BPFit(
        model=model,
        teams=teams,
        fitted_at=datetime.now(timezone.utc),
        n_matches=len(df),
        log_likelihood=log_l,
        converged=bool(res.success),
        n_iter=int(res.nit),
        match_counts={t: int(counts.get(t, 0)) for t in teams},
    )


def save_fit(fit_obj: BPFit, path: Path = FIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(fit_obj, f)


def load_fit(path: Path = FIT_PATH) -> BPFit:
    with open(path, "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    df = load_matches()
    wc26 = df[(df["tournament"] == "FIFA World Cup") & (df["date"] >= "2026-06-11")]
    print(f"Loaded {len(df):,} matches · WC 2026 group stage: {len(wc26)}")

    fit_obj = fit(df, verbose=True)
    save_fit(fit_obj)
    print(f"\nSaved fit.pkl")
    print(f"  n_matches = {fit_obj.n_matches:,}")
    print(f"  n_teams   = {len(fit_obj.teams)}")
    print(f"  converged = {fit_obj.converged}")
    print(f"  log-L     = {fit_obj.log_likelihood:.1f}")

    print("\nSample predictions:")
    for h, a, neu in [("Spain", "Morocco", True), ("Argentina", "France", True), ("Brazil", "Germany", True)]:
        try:
            xh, xa = fit_obj.expected_goals(h, a, neutral=neu)
            grid = fit_obj.predict_grid(h, a, neutral=neu)
            print(f"  {h} vs {a}: xG {xh:.2f}-{xa:.2f} | 1X2 {grid.home_win:.1%}/{grid.draw:.1%}/{grid.away_win:.1%}")
        except KeyError as e:
            print(f"  skip: {e}")
