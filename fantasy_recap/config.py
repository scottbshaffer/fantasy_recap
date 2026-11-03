"""Settings read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

DEFAULT_MODEL = "claude-sonnet-5-5"
SPORTS = ("basketball", "football")


def load_dotenv_if_present() -> None:
    """Load a .env file from the current directory if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dependency
        return
    load_dotenv()


def default_season(today: Optional[date] = None, sport: str = "basketball") -> int:
    """The ESPN season year that's current (or about to start) on `today`.

    ESPN names an NBA season by the year it ends (2026-27 is 2027), and the fantasy season starts
    in October, so from October on we use next year. An NFL season is named by the year it starts
    (2026 runs into early 2027), so in January and February we still mean last year's season.
    """
    today = today or date.today()
    if sport == "football":
        return today.year if today.month >= 3 else today.year - 1
    return today.year + 1 if today.month >= 10 else today.year


@dataclass
class EspnConfig:
    league_id: int
    year: int
    espn_s2: Optional[str] = None
    swid: Optional[str] = None
    sport: str = "basketball"

    @classmethod
    def from_env(cls, league_id: Optional[int] = None, year: Optional[int] = None,
                 sport: Optional[str] = None) -> "EspnConfig":
        sport = (sport or os.getenv("ESPN_SPORT") or "basketball").strip().lower()
        if sport not in SPORTS:
            raise ValueError(f"ESPN_SPORT must be one of {', '.join(SPORTS)} (got {sport!r}).")
        raw_id = league_id if league_id is not None else os.getenv("ESPN_LEAGUE_ID")
        if not raw_id:
            raise ValueError("Set ESPN_LEAGUE_ID (or pass --league-id). See .env.example.")
        raw_year = year if year is not None else os.getenv("ESPN_YEAR")
        swid = os.getenv("ESPN_SWID") or None
        if swid and not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        return cls(
            league_id=int(raw_id),
            year=int(raw_year) if raw_year else default_season(sport=sport),
            espn_s2=os.getenv("ESPN_S2") or None,
            swid=swid,
            sport=sport,
        )


@dataclass
class StoryConfig:
    api_key: Optional[str] = None
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> "StoryConfig":
        return cls(
            api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            model=model or os.getenv("FANTASY_RECAP_MODEL") or DEFAULT_MODEL,
        )
