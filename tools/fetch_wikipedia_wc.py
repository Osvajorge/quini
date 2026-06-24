"""Wikipedia WC2026 scraper — top scorers + tournament stats.

Wikipedia exposes parseable HTML tables without anti-bot. Used as
cross-validation against ESPN/StatsBomb data.

Output: docs/data/wikipedia_wc2026.json

Run: python -m tools.fetch_wikipedia_wc
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "wikipedia_wc2026.json"

WIKI_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup"
WIKI_STATS_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_statistics"


def _clean(s: str) -> str:
    if not isinstance(s, str):
        return s
    return re.sub(r"\[.*?\]", "", s).strip()


def fetch() -> None:
    out = {
        "top_scorers": [],
        "tournament_stats": {},
        "source": "Wikipedia",
        "status": "ok",
    }

    for url in (WIKI_STATS_URL, WIKI_URL):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 quini-bot"}, timeout=20)
            if r.status_code != 200:
                continue
        except Exception as e:
            out["status"] = f"err:{e}"
            continue

        try:
            import pandas as pd
            from io import StringIO
            tables = pd.read_html(StringIO(r.text))
        except Exception as e:
            out["status"] = f"parse-err:{e}"
            continue

        for t in tables:
            cols = [str(c) for c in t.columns.tolist()]
            cols_str = " ".join(cols).lower()

            # Top scorers table — usually has Player, Goals columns
            if ("goals" in cols_str or "g" in cols_str) and ("player" in cols_str or "name" in cols_str):
                for _, r in t.iterrows():
                    player = None
                    goals = None
                    for col in cols:
                        v = r.get(col)
                        if v is None:
                            continue
                        lc = col.lower()
                        if "player" in lc or "name" in lc:
                            player = _clean(str(v))
                        elif lc in ("goals", "g") or "goal" in lc:
                            try:
                                goals = int(float(str(v).split()[0]))
                            except (ValueError, IndexError):
                                pass
                    if player and goals is not None and goals > 0:
                        out["top_scorers"].append({"player": player, "goals": goals})
                if out["top_scorers"]:
                    break  # found top scorers table

        if out["top_scorers"]:
            break

    # Deduplicate + sort
    seen = {}
    for s in out["top_scorers"]:
        if s["player"] not in seen or seen[s["player"]] < s["goals"]:
            seen[s["player"]] = s["goals"]
    out["top_scorers"] = sorted(
        [{"player": k, "goals": v} for k, v in seen.items()],
        key=lambda x: -x["goals"],
    )[:25]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2, ensure_ascii=False)
    print(f"[wiki] {out['status']} · {len(out['top_scorers'])} top scorers")
    for s in out["top_scorers"][:5]:
        print(f"  {s['player']:30} {s['goals']} goles")


if __name__ == "__main__":
    fetch()
