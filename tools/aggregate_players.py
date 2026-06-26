"""Aggregate per-match leaders → per-player career totals in this tournament.

Reads docs/data/match_stats.json (built by fetch_match_stats).
Writes docs/data/player_stats.json with one entry per (player, team)
containing totals per stat category + matches played.

Run: python -m tools.aggregate_players
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "docs" / "data" / "match_stats.json"
OUT = ROOT / "docs" / "data" / "player_stats.json"


def _parse_val(v) -> float:
    if v is None:
        return 0.0
    s = str(v).replace(",", "").replace("%", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def aggregate() -> None:
    if not STATS.exists():
        print("[players] no match_stats.json; skipping")
        return
    data = json.load(open(STATS))

    # key: (player, team) → {category: {total, max, n_matches, headshot}}
    players: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "headshot": None,
        "matches": set(),
        "categories": defaultdict(lambda: {"total": 0.0, "max": 0.0, "n": 0}),
    })

    for fid, fixture in data.items():
        for L in fixture.get("leaders", []):
            player = (L.get("player") or "").strip()
            team = (L.get("team") or "").strip()
            cat = L.get("category") or ""
            if not player or not team or not cat:
                continue
            key = (player, team)
            rec = players[key]
            rec["matches"].add(fid)
            if L.get("headshot"):
                rec["headshot"] = L["headshot"]
            v = _parse_val(L.get("value"))
            c = rec["categories"][cat]
            c["total"] += v
            c["max"] = max(c["max"], v)
            c["n"] += 1

    # Flatten output
    out = []
    for (player, team), rec in players.items():
        cats = {k: {"total": round(v["total"], 1),
                    "max": round(v["max"], 1),
                    "appearances": v["n"]}
                for k, v in rec["categories"].items()}
        out.append({
            "player": player,
            "team": team,
            "headshot": rec["headshot"],
            "matches": len(rec["matches"]),
            "categories": cats,
        })

    def score(p):
        cats = p["categories"]
        goals = cats.get("Goals", {}).get("total", 0)
        assists = cats.get("Assists", {}).get("total", 0)
        shots = cats.get("Total Shots", {}).get("total", 0)
        return goals * 100 + assists * 50 + shots * 5
    out.sort(key=score, reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"players": out}, open(OUT, "w"), indent=2, ensure_ascii=False)
    print(f"[players] {len(out)} unique players aggregated")
    print(f"[players] top 10 scorers:")
    for p in out[:10]:
        g = p["categories"].get("Goals", {}).get("total", 0)
        a = p["categories"].get("Assists", {}).get("total", 0)
        print(f"  {p['player']:30} ({p['team']}) — {p['matches']}M · {int(g)}G {int(a)}A")


if __name__ == "__main__":
    aggregate()
