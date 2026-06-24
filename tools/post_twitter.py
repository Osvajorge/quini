"""Post the highest-edge BET to Twitter/X.

Reads docs/data/social_posts.json + .posted_tweets.json (state file) to
avoid duplicates. Posts via Twitter API v2 (OAuth 1.0a User Context).

Required env vars (set as GitHub Actions secrets):
  TWITTER_API_KEY        — Consumer Key
  TWITTER_API_SECRET     — Consumer Secret
  TWITTER_ACCESS_TOKEN   — User access token (with write scope)
  TWITTER_ACCESS_SECRET  — User access secret

Skips gracefully if any secret is missing (just logs). Useful for staging.

Run: python -m tools.post_twitter [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOCIAL = ROOT / "docs" / "data" / "social_posts.json"
STATE = ROOT / ".posted_tweets.json"

MIN_EDGE = 10.0  # only auto-tweet picks with at least 10% edge


def _load_state() -> set[str]:
    if STATE.exists():
        try:
            return set(json.load(open(STATE)).get("posted", []))
        except Exception:
            return set()
    return set()


def _save_state(posted: set[str]) -> None:
    json.dump({"posted": sorted(posted)}, open(STATE, "w"), indent=2)


def _post(text: str, creds: dict) -> bool:
    """Post via OAuth 1.0a with requests-oauthlib. Returns True on success."""
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        print("[tweet] requests-oauthlib not installed")
        return False
    sess = OAuth1Session(
        creds["api_key"], creds["api_secret"],
        creds["access_token"], creds["access_secret"],
    )
    r = sess.post(
        "https://api.twitter.com/2/tweets",
        json={"text": text},
        timeout=20,
    )
    if r.status_code == 201:
        return True
    print(f"[tweet] failed: {r.status_code} {r.text[:200]}")
    return False


def post(dry_run: bool = False) -> None:
    if not SOCIAL.exists():
        print("[tweet] no social_posts.json; skipping")
        return

    creds = {
        "api_key": os.environ.get("TWITTER_API_KEY"),
        "api_secret": os.environ.get("TWITTER_API_SECRET"),
        "access_token": os.environ.get("TWITTER_ACCESS_TOKEN"),
        "access_secret": os.environ.get("TWITTER_ACCESS_SECRET"),
    }
    has_creds = all(creds.values())
    if not has_creds and not dry_run:
        print("[tweet] missing TWITTER_* env vars — skipping (set as secrets to enable)")
        return

    posts = json.load(open(SOCIAL))
    if not isinstance(posts, list):
        posts = []

    posted = _load_state()
    candidates = [
        p for p in posts
        if p.get("edge", 0) >= MIN_EDGE
        and 0 <= (p.get("hours_until") or 999) <= 24
        and p["fixture_id"] not in posted
    ]
    if not candidates:
        print(f"[tweet] no fresh BETs (>= {MIN_EDGE}% edge in next 24h)")
        return

    # Take highest-edge new pick
    best = max(candidates, key=lambda p: p["edge"])
    text = best["formats"]["twitter"]
    print(f"[tweet] candidate: {best['home']} vs {best['away']} +{best['edge']}%")
    print(f"[tweet] text:\n---\n{text}\n---")

    if dry_run:
        print("[tweet] DRY RUN — not posting")
        return

    if _post(text, creds):
        posted.add(best["fixture_id"])
        _save_state(posted)
        print(f"[tweet] ✓ posted {best['fixture_id']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    post(dry_run=args.dry_run)
