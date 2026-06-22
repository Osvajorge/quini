"""Re-fit Dixon-Coles model and save fit.pkl. Run from project root:
    python scripts/refit.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.data_loader import load_matches
from model.dixon_coles import fit, save_fit

df = load_matches()
wc26 = df[(df['tournament'] == 'FIFA World Cup') & (df['date'] >= '2026-06-11')]
print(f"Loaded {len(df):,} matches · {df.date.min().date()} → {df.date.max().date()}")
print(f"Teams: {df.home_team.nunique()}")
print(f"WC 2026 group stage results in fit: {len(wc26)}")

fit_obj = fit(df, verbose=True)
save_fit(fit_obj)

print(f"\nSaved fit.pkl")
print(f"  n_matches = {fit_obj.n_matches:,}")
print(f"  n_teams   = {len(fit_obj.teams)}")
print(f"  converged = {fit_obj.converged}")
print(f"  log-L     = {fit_obj.log_likelihood:.1f}")
print(f"  γ (home)  = {fit_obj.gamma:+.4f}")
print(f"  ρ (DC)    = {fit_obj.rho:+.4f}")
