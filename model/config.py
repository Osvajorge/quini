"""Loads env vars from .env. Import from here, never read os.environ directly."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The Odds API: two separate keys (different account/plan).
# Paid key has historical access (use for backtest); free key for live polling.
THE_ODDS_API_KEY_PAID = os.getenv("THE_ODDS_API_KEY_PAID", "")
THE_ODDS_API_KEY_FREE = os.getenv("THE_ODDS_API_KEY_FREE", "")

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
REFIT_TOKEN = os.getenv("REFIT_TOKEN", "")


def require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(
            f"Missing env var {name}. Add it to {ROOT}/.env (see .env.example)."
        )
    return value


if __name__ == "__main__":
    print(f".env loaded from {ROOT}/.env")
    print(f"THE_ODDS_API_KEY_PAID: {'set ✓' if THE_ODDS_API_KEY_PAID else 'empty ✗'}")
    print(f"THE_ODDS_API_KEY_FREE: {'set ✓' if THE_ODDS_API_KEY_FREE else 'empty ✗'}")
    print(f"API_FOOTBALL_KEY:      {'set ✓' if API_FOOTBALL_KEY else 'empty ✗'}")
    print(f"REFIT_TOKEN:           {'set ✓' if REFIT_TOKEN else 'empty ✗'}")
