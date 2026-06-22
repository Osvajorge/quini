"""Dixon-Coles MLE fit on weighted match data.

Parametrization (log scale):
    log λ_home = α_h + β_a + γ · (1 - neutral)
    log λ_away = α_a + β_h

α = attack strength, β = defensive frailty (higher β → leakier defense),
γ = home advantage, ρ = Dixon-Coles low-score correction.

Identifiability is enforced after fit by mean-centering α and β
(the log-likelihood is invariant under α ← α + c, β ← β - c).
"""
from __future__ import annotations

import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

from model.data_loader import load_matches, team_match_counts

FIT_PATH = Path(__file__).resolve().parent.parent / "data" / "fit.pkl"

# Bounds keep optimizer numerically stable.
ALPHA_BOUNDS = (-3.0, 3.0)
BETA_BOUNDS = (-3.0, 3.0)
GAMMA_BOUNDS = (-1.0, 1.5)
RHO_BOUNDS = (-0.2, 0.2)


@dataclass
class DCFit:
    alpha: dict[str, float]
    beta: dict[str, float]
    gamma: float
    rho: float
    teams: list[str]
    fitted_at: datetime
    n_matches: int
    log_likelihood: float
    converged: bool
    n_iter: int
    match_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "gamma": self.gamma,
            "rho": self.rho,
            "teams": self.teams,
            "fitted_at": self.fitted_at.isoformat(),
            "n_matches": self.n_matches,
            "log_likelihood": self.log_likelihood,
            "converged": self.converged,
            "n_iter": self.n_iter,
        }

    def expected_goals(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        if home not in self.alpha or away not in self.alpha:
            raise KeyError(f"Unknown team(s): {home}, {away}")
        ha = 0.0 if neutral else self.gamma
        log_lam_h = self.alpha[home] + self.beta[away] + ha
        log_lam_a = self.alpha[away] + self.beta[home]
        return float(np.exp(log_lam_h)), float(np.exp(log_lam_a))


def _poisson_logpmf(k: np.ndarray, lam: np.ndarray) -> np.ndarray:
    # log(λ^k · e^-λ / k!) = k·log(λ) - λ - log(k!)
    lam = np.clip(lam, 1e-10, None)
    return k * np.log(lam) - lam - gammaln(k + 1.0)


def _dc_log_correction(
    h: np.ndarray, a: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray, rho: float
) -> np.ndarray:
    """log τ(h, a) for the four corner cases; 0 elsewhere."""
    factor = np.ones_like(lam_h)

    m00 = (h == 0) & (a == 0)
    m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0)
    m11 = (h == 1) & (a == 1)

    factor[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    factor[m01] = 1.0 + lam_h[m01] * rho
    factor[m10] = 1.0 + lam_a[m10] * rho
    factor[m11] = 1.0 - rho

    # Guard against rho values that push τ ≤ 0 for extreme λ.
    factor = np.clip(factor, 1e-10, None)
    return np.log(factor)


def _build_arrays(df: pd.DataFrame, teams: list[str]) -> dict:
    idx = {t: i for i, t in enumerate(teams)}
    h_idx = df["home_team"].map(idx).to_numpy()
    a_idx = df["away_team"].map(idx).to_numpy()
    if np.isnan(h_idx).any() or np.isnan(a_idx).any():
        raise ValueError("Match references team not in team list.")
    return {
        "h_idx": h_idx.astype(int),
        "a_idx": a_idx.astype(int),
        "hs": df["home_score"].to_numpy().astype(int),
        "as_": df["away_score"].to_numpy().astype(int),
        "w": df["weight"].to_numpy(),
        "is_home": (~df["neutral"].to_numpy()).astype(float),
    }


def _neg_log_likelihood(params: np.ndarray, n: int, data: dict) -> float:
    alpha = params[:n]
    beta = params[n : 2 * n]
    gamma = params[-2]
    rho = params[-1]

    h_idx = data["h_idx"]
    a_idx = data["a_idx"]
    hs = data["hs"]
    as_ = data["as_"]
    w = data["w"]
    is_home = data["is_home"]

    log_lam_h = alpha[h_idx] + beta[a_idx] + gamma * is_home
    log_lam_a = alpha[a_idx] + beta[h_idx]
    lam_h = np.exp(log_lam_h)
    lam_a = np.exp(log_lam_a)

    ll = (
        _poisson_logpmf(hs, lam_h)
        + _poisson_logpmf(as_, lam_a)
        + _dc_log_correction(hs, as_, lam_h, lam_a, rho)
    )
    return float(-np.sum(w * ll))


def fit(
    df: pd.DataFrame | None = None,
    init_gamma: float = 0.3,
    init_rho: float = -0.05,
    verbose: bool = True,
) -> DCFit:
    """Fit Dixon-Coles MLE to a weighted match DataFrame.

    Parameters
    ----------
    df : DataFrame, optional
        Output of `data_loader.load_matches`. If None, calls it with defaults.
        Required columns: home_team, away_team, home_score, away_score,
        neutral (bool), weight (float).
    init_gamma : float
        Initial home advantage (log scale). 0.3 ≈ home goal rate × 1.35.
    init_rho : float
        Initial DC correction. Empirically ~−0.05 for international football.
    verbose : bool
        Print fit summary.

    Returns
    -------
    DCFit
        Contains α and β by team, γ, ρ, and the team match counts used for
        sample-size confidence in downstream tools.

    Notes
    -----
    Optimisation: L-BFGS-B with bounds on each parameter. Mean-centres α and
    β post-fit to enforce identifiability (the log-likelihood is invariant
    under α → α + c, β → β − c).

    Cost: roughly 90 s for ~20k matches on Apple Silicon. Cache via `save_fit`.
    """
    if df is None:
        df = load_matches()

    teams = sorted(set(df["home_team"]) | set(df["away_team"]))
    n = len(teams)
    data = _build_arrays(df, teams)

    x0 = np.zeros(2 * n + 2)
    x0[-2] = init_gamma
    x0[-1] = init_rho

    bounds = (
        [ALPHA_BOUNDS] * n
        + [BETA_BOUNDS] * n
        + [GAMMA_BOUNDS, RHO_BOUNDS]
    )

    t0 = time.perf_counter()
    res = minimize(
        _neg_log_likelihood,
        x0,
        args=(n, data),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 5000, "maxfun": 150000, "ftol": 1e-10, "gtol": 1e-5},
    )
    elapsed = time.perf_counter() - t0

    alpha_vec = res.x[:n]
    beta_vec = res.x[n : 2 * n]
    gamma = float(res.x[-2])
    rho = float(res.x[-1])

    # Mean-center α and β so they are uniquely identified.
    alpha_vec = alpha_vec - alpha_vec.mean()
    beta_vec = beta_vec - beta_vec.mean()

    if verbose:
        print(f"fit done in {elapsed:.1f}s · iters={res.nit} · converged={res.success}")
        print(f"  γ = {gamma:+.4f} · ρ = {rho:+.4f} · log-L = {-res.fun:.1f}")

    counts = team_match_counts(df).to_dict()

    return DCFit(
        alpha={t: float(v) for t, v in zip(teams, alpha_vec)},
        beta={t: float(v) for t, v in zip(teams, beta_vec)},
        gamma=gamma,
        rho=rho,
        teams=teams,
        fitted_at=datetime.now(timezone.utc),
        n_matches=len(df),
        log_likelihood=float(-res.fun),
        converged=bool(res.success),
        n_iter=int(res.nit),
        match_counts={t: int(counts.get(t, 0)) for t in teams},
    )


def save_fit(fit_obj: DCFit, path: Path = FIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(fit_obj, f)


def load_fit(path: Path = FIT_PATH) -> DCFit:
    with open(path, "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    # When run as a script, DCFit's __module__ becomes "__main__", which
    # breaks pickle loading from other entry points. Pin to the real module.
    DCFit.__module__ = "model.dixon_coles"

    df = load_matches()
    fit_obj = fit(df)
    save_fit(fit_obj)

    print()
    print("sanity checks:")
    assert 0.0 < fit_obj.gamma < 1.0, f"gamma out of expected range: {fit_obj.gamma}"
    assert -0.2 < fit_obj.rho < 0.05, f"rho out of expected range: {fit_obj.rho}"

    # Top attackers and worst defenses
    alpha_series = pd.Series(fit_obj.alpha).sort_values(ascending=False)
    beta_series = pd.Series(fit_obj.beta).sort_values(ascending=False)
    print("\ntop 10 attacks (α):")
    print(alpha_series.head(10).round(3))
    print("\nworst 10 defenses (β, higher = leakier):")
    print(beta_series.head(10).round(3))
    print("\nbest 10 defenses (β):")
    print(beta_series.tail(10).round(3))

    print("\nsample predictions (xG):")
    for h, a, neu in [
        ("Spain", "Cape Verde", False),
        ("Brazil", "Morocco", False),
        ("Argentina", "Algeria", False),
        ("Mexico", "United States", True),
    ]:
        try:
            xh, xa = fit_obj.expected_goals(h, a, neutral=neu)
            print(f"  {h} vs {a} (neutral={neu}): {xh:.2f} - {xa:.2f}")
        except KeyError as e:
            print(f"  skip: {e}")
