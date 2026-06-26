"""Multi-league / tournament configuration.

Each league defines its data source, ESPN endpoints, draw rate,
half-life, and tournament weight overrides. Designed for expansion
beyond WC2026 into club leagues and other international tournaments.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LeagueConfig:
    key: str
    name: str
    odds_api_sport: str
    espn_slug: str
    draw_rate: float  # observed historical draw rate for draw inflation
    half_life_days: int  # time decay for match weighting
    min_date: str  # earliest data to include in fit
    neutral_venue: bool  # whether matches are typically neutral
    tournament_weight: float  # base weight for this competition
    results_filter: str | None = None  # filter value in results.csv tournament column


LEAGUES: dict[str, LeagueConfig] = {
    "fifa_world": LeagueConfig(
        key="fifa_world",
        name="FIFA World Cup 2026",
        odds_api_sport="soccer_fifa_world_cup",
        espn_slug="fifa.world",
        draw_rate=0.23,
        half_life_days=180,
        min_date="2005-01-01",
        neutral_venue=True,
        tournament_weight=5.0,
        results_filter="FIFA World Cup",
    ),
    "premier_league": LeagueConfig(
        key="premier_league",
        name="English Premier League",
        odds_api_sport="soccer_epl",
        espn_slug="eng.1",
        draw_rate=0.25,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=4.0,
    ),
    "la_liga": LeagueConfig(
        key="la_liga",
        name="La Liga",
        odds_api_sport="soccer_spain_la_liga",
        espn_slug="esp.1",
        draw_rate=0.26,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=4.0,
    ),
    "liga_mx": LeagueConfig(
        key="liga_mx",
        name="Liga MX",
        odds_api_sport="soccer_mexico_ligamx",
        espn_slug="mex.1",
        draw_rate=0.27,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=3.5,
    ),
    "liga_argentina": LeagueConfig(
        key="liga_argentina",
        name="Liga Profesional Argentina",
        odds_api_sport="soccer_argentina_primera_division",
        espn_slug="arg.1",
        draw_rate=0.26,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=3.5,
    ),
    "mls": LeagueConfig(
        key="mls",
        name="MLS",
        odds_api_sport="soccer_usa_mls",
        espn_slug="usa.1",
        draw_rate=0.22,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=3.0,
    ),
    "champions_league": LeagueConfig(
        key="champions_league",
        name="UEFA Champions League",
        odds_api_sport="soccer_uefa_champs_league",
        espn_slug="uefa.champions",
        draw_rate=0.24,
        half_life_days=150,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=5.0,
    ),
    "bundesliga": LeagueConfig(
        key="bundesliga",
        name="Bundesliga",
        odds_api_sport="soccer_germany_bundesliga",
        espn_slug="ger.1",
        draw_rate=0.24,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=4.0,
    ),
    "serie_a": LeagueConfig(
        key="serie_a",
        name="Serie A",
        odds_api_sport="soccer_italy_serie_a",
        espn_slug="ita.1",
        draw_rate=0.27,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=4.0,
    ),
    "ligue_1": LeagueConfig(
        key="ligue_1",
        name="Ligue 1",
        odds_api_sport="soccer_france_ligue_one",
        espn_slug="fra.1",
        draw_rate=0.25,
        half_life_days=120,
        min_date="2015-01-01",
        neutral_venue=False,
        tournament_weight=3.5,
    ),
}

ACTIVE_LEAGUE = "fifa_world"


def get_league(key: str | None = None) -> LeagueConfig:
    return LEAGUES[key or ACTIVE_LEAGUE]


def espn_base(league: LeagueConfig) -> str:
    return f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league.espn_slug}"
