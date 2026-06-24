"""Sofascore unofficial API — currently BLOCKED by Cloudflare 403.

Their API requires bypass (cloudscraper, FlareSolverr, or paid residential
proxy). Not viable in free GitHub Actions cron.

Alternatives if needed later:
  - cloudscraper python lib (works ~50% of time, breaks on anti-bot updates)
  - FlareSolverr docker container (sidecar, heavy infra)
  - ScraperAPI / ScrapingBee paid proxy (~$30/mo for ~5k req/day)

If you have a workaround, replace the body of `fetch()` with actual
requests. Output schema:

  {
    "fixtures": {
      "<quini_fixture_id>": {
        "sofascore_id": ...,
        "xg_home": ..., "xg_away": ...,
        "ratings": [{"player": ..., "team": ..., "rating": ...}, ...],
        "fetched_at": "..."
      }
    }
  }
"""
from __future__ import annotations
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "sofascore.json"


def fetch() -> None:
    print("[sofascore] skipped — Cloudflare 403 (needs cloudscraper or paid proxy)")
    return


if __name__ == "__main__":
    fetch()
