"""Generate /docs/og-card.png — social share preview (1200×630).

Pulls live stats from docs/data/history.json. Run in CI after generate
to keep the OG card fresh.

Run: python -m tools.generate_og_card
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "docs" / "data" / "history.json"
OUT = ROOT / "docs" / "og-card.png"

# 1200×630 = standard OG / Twitter card
W, H = 1200, 630

# Dark theme matching the app
BG_TOP = (5, 10, 20)
BG_BOT = (10, 25, 18)
PANEL = (14, 25, 41)
INK = (241, 245, 249)
MUTE = (100, 116, 139)
MUTE2 = (51, 65, 85)
GREEN = (16, 185, 129)
GREEN2 = (52, 211, 153)
ACCENT = (8, 145, 178)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try system fonts; fall back to PIL default if none."""
    candidates_bold = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "Arial Bold.ttf",
    ]
    candidates_reg = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "Arial.ttf",
    ]
    paths = candidates_bold if bold else candidates_reg
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_gradient_bg(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """Vertical gradient BG_TOP → BG_BOT."""
    for y in range(h):
        ratio = y / h
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * ratio)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * ratio)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def _draw_glow(img: Image.Image, cx: int, cy: int, radius: int, color: tuple) -> None:
    """Soft radial glow via alpha composite."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for i, alpha in enumerate(range(60, 0, -3)):
        rr = radius * (i + 1) // 20
        odraw.ellipse(
            [cx - rr, cy - rr, cx + rr, cy + rr],
            fill=(color[0], color[1], color[2], alpha // 4),
        )
    img.alpha_composite(overlay)


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, r: int, fill=None, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def generate() -> None:
    summary = {}
    if HISTORY.exists():
        try:
            with open(HISTORY) as f:
                data = json.load(f)
            summary = data.get("summary", {})
        except Exception:
            summary = {}

    roi = summary.get("roi", 0) or 0
    win_rate = summary.get("win_rate", 0) or 0
    total_bets = summary.get("total_bets", 0) or 0
    avg_clv = summary.get("avg_clv_pct")

    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    _draw_gradient_bg(draw, W, H)

    # Top-center glow
    _draw_glow(img, W // 2, 0, 600, GREEN)
    draw = ImageDraw.Draw(img)  # rebind after composite

    # Top accent bar
    draw.rectangle([0, 0, W, 6], fill=GREEN)

    # ── Logo + brand ──
    logo_x, logo_y = 60, 60
    _rounded_rect(draw, [logo_x, logo_y, logo_x + 64, logo_y + 64], r=16, fill=GREEN)
    f_logo = _load_font(36, bold=True)
    draw.text((logo_x + 18, logo_y + 12), "Q", font=f_logo, fill=(255, 255, 255))

    f_brand = _load_font(40, bold=True)
    draw.text((logo_x + 80, logo_y + 6), "Quini", font=f_brand, fill=INK)
    f_tagline = _load_font(20)
    draw.text((logo_x + 80, logo_y + 52), "Mundial 2026", font=f_tagline, fill=MUTE)

    # ── Hero text ──
    f_hero = _load_font(60, bold=True)
    f_hero2 = _load_font(60, bold=True)
    draw.text((60, 200), "Apuestas con", font=f_hero, fill=INK)
    draw.text((60, 270), "ventaja matemática.", font=f_hero2, fill=GREEN2)

    # Subtitle
    f_sub = _load_font(22)
    draw.text(
        (60, 360),
        "Modelo Bivariate-Poisson + Elo · Tracking público de ROI y CLV",
        font=f_sub,
        fill=MUTE,
    )

    # ── Stats row ──
    stats = [
        ("ROI", f"{'+' if roi >= 0 else ''}{roi}%", GREEN2 if roi >= 0 else (245, 158, 11)),
        ("ACIERTO", f"{win_rate}%", INK),
        ("APUESTAS", f"{total_bets}", INK),
        ("CLV", f"{'+' if (avg_clv or 0) >= 0 else ''}{avg_clv}%" if avg_clv is not None else "—", GREEN2 if (avg_clv or 0) >= 0 else (245, 158, 11)),
    ]
    card_w, card_h = 250, 130
    gap = 20
    start_x = (W - (card_w * len(stats) + gap * (len(stats) - 1))) // 2
    base_y = 440
    f_label = _load_font(16)
    f_value = _load_font(48, bold=True)
    for i, (label, value, color) in enumerate(stats):
        x = start_x + i * (card_w + gap)
        _rounded_rect(draw, [x, base_y, x + card_w, base_y + card_h], r=18, fill=PANEL, outline=MUTE2)
        draw.text((x + 24, base_y + 18), label, font=f_label, fill=MUTE)
        draw.text((x + 24, base_y + 50), value, font=f_value, fill=color)

    # Bottom URL
    f_url = _load_font(18)
    draw.text((60, H - 40), "kini.bet", font=f_url, fill=MUTE)

    img.convert("RGB").save(OUT, "PNG", optimize=True)
    print(f"✓ OG card written: {OUT}")
    print(f"  Stats: ROI={roi}% win_rate={win_rate}% bets={total_bets} clv={avg_clv}")


if __name__ == "__main__":
    generate()
