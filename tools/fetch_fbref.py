"""FBref international tournament stats — best-effort scraper.

FBref uses Cloudflare anti-bot. Direct requests + cloudscraper both 403.
Reliable bypass requires:
  - soccerdata package (uses Selenium, ~30s/page, heavy for cron)
  - paid proxy (ScraperAPI / ScrapingBee)
  - manual download

This module tries cloudscraper first. If 403, skips gracefully and writes
a stub so downstream code knows the data is unavailable.

To populate manually:
  python -m tools.fetch_fbref --selenium  (requires soccerdata installed)

Output: docs/data/fbref_teams.json
  {
    "teams": [{"team": ..., "xg_for": ..., "xg_against": ..., ...}],
    "source": "FBref",
    "status": "ok" | "blocked" | "stale"
  }
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "fbref_teams.json"

# FBref World Cup 2026 stats URL
WC2026_URL = "https://fbref.com/en/comps/1/2026/2026-FIFA-World-Cup-Stats"


def _try_cloudscraper() -> tuple[int, str]:
    try:
        import cloudscraper
    except ImportError:
        return 0, "cloudscraper-not-installed"
    try:
        s = cloudscraper.create_scraper()
        r = s.get(WC2026_URL, timeout=30)
        return r.status_code, r.text
    except Exception as e:
        return 0, f"err:{e}"


def _try_soccerdata() -> dict | None:
    try:
        import soccerdata as sd
    except ImportError:
        return None
    try:
        fbref = sd.FBref(leagues="INT-World Cup", seasons="2026")
        df = fbref.read_team_season_stats(stat_type="standard")
        if df is None or len(df) == 0:
            return None
        return df.reset_index().to_dict(orient="records")
    except Exception as e:
        print(f"[fbref] soccerdata err: {e}")
        return None


def fetch(use_selenium: bool = False) -> None:
    teams_out: list[dict] = []
    status = "blocked"

    if use_selenium:
        rows = _try_soccerdata()
        if rows:
            for r in rows:
                teams_out.append({
                    "team": r.get("team") or r.get("squad"),
                    "matches": r.get("playing_time_mp", r.get("MP")),
                    "goals_for": r.get("performance_gls", r.get("Gls")),
                    "goals_against": r.get("performance_ga", r.get("GA")),
                    "xg_for": r.get("expected_xg", r.get("xG")),
                    "xg_against": r.get("expected_xga", r.get("xGA")),
                    "xg_diff": r.get("expected_xgd", r.get("xGD")),
                })
            status = "ok"
        else:
            status = "selenium-failed"
    else:
        code, body = _try_cloudscraper()
        if code == 200 and "<table" in body:
            try:
                import pandas as pd
                from io import StringIO
                tables = pd.read_html(StringIO(body))
                for t in tables:
                    if any("xG" in str(c) for c in t.columns.tolist()):
                        for _, r in t.iterrows():
                            teams_out.append({
                                "team": r.get("Squad") or r.get("Team"),
                                "matches": r.get("MP"),
                                "goals_for": r.get("Gls"),
                                "xg_for": r.get("xG"),
                            })
                        break
                status = "ok" if teams_out else "no-xg-table"
            except Exception as e:
                status = f"parse-err:{e}"
        else:
            status = f"http-{code}"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"teams": teams_out, "source": "FBref WC2026", "status": status},
              open(OUT, "w"), indent=2)
    print(f"[fbref] {status} · {len(teams_out)} teams")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selenium", action="store_true", help="Use soccerdata+Selenium (heavy)")
    args = ap.parse_args()
    fetch(use_selenium=args.selenium)
