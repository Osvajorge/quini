"""Generate ready-to-post social media content from current predictions.

Outputs:
- docs/data/social_posts.json — structured (one item per BET, multi-format)
- /tmp/social_posts.md — human-readable to copy-paste

Until Twitter/Telegram API integration is wired, you can browse this file
manually and post the best ones. Works as a daily content prompt.

Run: python -m tools.generate_social_posts
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRED = ROOT / "docs" / "data" / "predictions.json"
OUT_JSON = ROOT / "docs" / "data" / "social_posts.json"
OUT_MD = Path("/tmp/social_posts.md")

# Spanish team-name overrides for nicer copy
TEAM_NAMES_ES = {
    "United States": "USA", "South Africa": "Sudáfrica", "South Korea": "Corea del Sur",
    "Saudi Arabia": "Arabia Saudita", "Czech Republic": "Chequia", "Ivory Coast": "Costa de Marfil",
}


def _team(name: str) -> str:
    return TEAM_NAMES_ES.get(name, name)


def _format_pick(f: dict, b: dict) -> str:
    desc = b.get("description_es") or b.get("market", "")
    desc = desc.replace(f["home"], _team(f["home"])).replace(f["away"], _team(f["away"]))
    return desc


def _hours_until(commence_time: str) -> float:
    try:
        ct = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        return (ct - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except Exception:
        return 999.0


def generate() -> None:
    data = json.load(open(PRED))
    fixtures = data.get("fixtures", [])

    posts = []
    md_lines = ["# Quini · Posts del día", "", f"_Generado {datetime.now(timezone.utc).isoformat(timespec='minutes')}_", ""]

    for fx in fixtures:
        if fx.get("completed") or fx.get("is_live"):
            continue
        hrs = _hours_until(fx.get("commence_time", ""))
        if hrs < 0 or hrs > 48:
            continue

        bets = fx.get("bets", [])
        if not bets:
            continue

        best = max(bets, key=lambda b: b.get("edge", 0))
        edge = best.get("edge", 0)
        if edge < 10:  # only highlight strong edges
            continue

        home, away = _team(fx["home"]), _team(fx["away"])
        odds = best.get("best_odds") or best.get("odds")
        book = best.get("best_book", "").strip()
        pick = _format_pick(fx, best)
        kelly_pct = best.get("kelly_pct", 0)

        # Pre-match: 2h before kickoff is the prime window
        is_imminent = 0 <= hrs <= 4

        # ── Twitter/X (280 chars) ──
        emoji = "🚨" if is_imminent else "⚽"
        twitter = (
            f"{emoji} {home} vs {away}\n"
            f"Pick: {pick}\n"
            f"+{edge}% ventaja · @{odds} ({book or 'best book'})\n"
            f"Kelly: {kelly_pct}% banca\n\n"
            f"Modelo: kini.bet"
        )
        if len(twitter) > 280:
            twitter = twitter[:277] + "..."

        # ── WhatsApp / Telegram (longer, emoji-friendly) ──
        whatsapp = (
            f"⚽ *{home} vs {away}*\n"
            f"🎯 *{pick}*\n"
            f"📈 +{edge}% ventaja\n"
            f"💰 @{odds} ({book or 'mejor cuota'})\n"
            f"💼 Kelly: {kelly_pct}% de tu banca\n\n"
            f"Análisis completo: kini.bet"
        )

        # ── Instagram caption ──
        instagram = (
            f"⚽ {home} vs {away}\n\n"
            f"Nuestro modelo dice: {pick}\n\n"
            f"Encontramos una ventaja de +{edge}% sobre las cuotas del mercado. "
            f"Si tienes una banca de €1000, deberías apostar ~€{int(10 * kelly_pct)} "
            f"según Quarter-Kelly.\n\n"
            f"#Mundial2026 #ApuestasDeportivas #{home.replace(' ','')} #{away.replace(' ','')}"
        )

        posts.append({
            "fixture_id": fx["id"],
            "home": fx["home"], "away": fx["away"],
            "commence_time": fx["commence_time"],
            "hours_until": round(hrs, 1),
            "pick": pick,
            "edge": edge,
            "odds": odds,
            "book": book,
            "kelly_pct": kelly_pct,
            "is_imminent": is_imminent,
            "formats": {
                "twitter": twitter,
                "whatsapp": whatsapp,
                "instagram": instagram,
            },
        })

        md_lines += [
            f"## {home} vs {away}  ·  edge +{edge}%",
            f"_kickoff en {hrs:.1f}h_  ·  pick: **{pick}**  ·  @{odds} en {book}",
            "",
            "### Twitter",
            "```",
            twitter,
            "```",
            "",
            "### WhatsApp / Telegram",
            "```",
            whatsapp,
            "```",
            "",
            "### Instagram",
            "```",
            instagram,
            "```",
            "---",
            "",
        ]

    posts.sort(key=lambda p: -p["edge"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    json.dump(posts, open(OUT_JSON, "w"), indent=2, ensure_ascii=False)
    OUT_MD.write_text("\n".join(md_lines))
    print(f"✓ {len(posts)} social posts generated")
    print(f"  JSON: {OUT_JSON}")
    print(f"  MD:   {OUT_MD}")


if __name__ == "__main__":
    generate()
