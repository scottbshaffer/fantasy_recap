"""Read a week of matchups from an ESPN fantasy basketball league.

`fetch_week` talks to ESPN through the espn-api package. `summarize_box_scores`
turns espn-api box score objects into our own `WeekSummary`, and only relies on
attributes, so tests can feed it simple fakes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import EspnConfig
from .games import fetch_big_games
from .models import Matchup, PlayerLine, TeamWeek, WeekSummary


log = logging.getLogger(__name__)


class EspnError(RuntimeError):
    """Raised when the league can't be read (bad id, private league without cookies...)."""


def connect(config: EspnConfig):
    """Return an espn_api basketball League for the given config."""
    try:
        from espn_api.basketball import League
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise EspnError("The espn-api package is not installed. Run: pip install -r requirements.txt") from exc

    try:
        return League(
            league_id=config.league_id,
            year=config.year,
            espn_s2=config.espn_s2,
            swid=config.swid,
        )
    except Exception as exc:  # espn-api raises a mix of custom and generic errors
        hint = ""
        if not (config.espn_s2 and config.swid):
            hint = " If the league is private, set ESPN_S2 and ESPN_SWID (see README)."
        raise EspnError(f"Couldn't load ESPN league {config.league_id} for {config.year}: {exc}.{hint}") from exc


def fetch_week(
    config: EspnConfig,
    week: Optional[int] = None,
    league: Any = None,
    previous: bool = False,
    big_games: int = 10,
) -> WeekSummary:
    """Fetch the matchups for a matchup period.

    Defaults to the current week; `previous=True` picks the week before it, which
    is the one that just finished when run on a Monday. `big_games` is how many
    top single-game performances to include (0 to skip; it costs one ESPN request
    per day of the week).
    """
    league = league or connect(config)
    current = getattr(league, "currentMatchupPeriod", None)
    if week is None:
        week = current
        if previous and current is not None:
            week = current - 1
    elif current is not None and week > current:
        raise EspnError(f"Week {week} hasn't started yet (the league is on week {current}).")
    if week is None or week < 1:
        raise EspnError("There's no finished week to recap yet.")

    box_scores = league.box_scores(matchup_period=week, matchup_total=True)
    settings = getattr(league, "settings", None)
    summary = summarize_box_scores(
        box_scores,
        league_name=getattr(settings, "name", None) or f"League {config.league_id}",
        season=config.year,
        week=week,
        scoring_type=getattr(settings, "scoring_type", None),
    )
    if big_games:
        try:
            summary.big_games = fetch_big_games(
                league, week, use_fantasy_points=summary.scoring == "points", limit=big_games
            )
        except Exception as exc:  # a missing extra shouldn't sink the recap
            log.warning("Couldn't load single-game performances: %s", exc)
    return summary


def export_path(summary: WeekSummary, directory: Path) -> Path:
    return Path(directory) / f"{summary.season}-week-{summary.week:02d}.json"


def export_summary(summary: WeekSummary, directory: Path = Path("summaries")) -> Path:
    """Write the week's structured summary as JSON and return the file path."""
    path = export_path(summary, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = summary.to_dict()
    data["status"] = (
        "final" if summary.real_matchups and all(m.status == "final" for m in summary.real_matchups)
        else "in_progress"
    )
    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _scoring_kind(scoring_type: Optional[str], box_scores: list) -> str:
    if scoring_type:
        return "categories" if "CATEGOR" in scoring_type.upper() else "points"
    if box_scores and hasattr(box_scores[0], "home_wins"):
        return "categories"
    return "points"


def _record(team: Any) -> str:
    wins = getattr(team, "wins", None)
    if wins is None:
        return ""
    return f"{wins}-{getattr(team, 'losses', 0)}-{getattr(team, 'ties', 0)}"


def _players(lineup: Iterable[Any]) -> tuple[list[PlayerLine], list[PlayerLine]]:
    starters: list[PlayerLine] = []
    bench: list[PlayerLine] = []
    for p in lineup or []:
        line = PlayerLine(
            name=getattr(p, "name", "Unknown"),
            slot=getattr(p, "slot_position", "") or "",
            points=round(float(getattr(p, "points", 0) or 0), 2),
            pro_team=getattr(p, "proTeam", "") or "",
            injury_status=getattr(p, "injuryStatus", "") or "",
        )
        (bench if line.on_bench else starters).append(line)
    return starters, bench


def _team_week(box: Any, side: str, scoring: str) -> Optional[TeamWeek]:
    team = getattr(box, f"{side}_team", None)
    # espn-api leaves the team id (0) in place for a bye.
    if not team or isinstance(team, int):
        return None
    starters, bench = _players(getattr(box, f"{side}_lineup", []))
    tw = TeamWeek(
        name=getattr(team, "team_name", "Unknown"),
        abbrev=getattr(team, "team_abbrev", ""),
        record=_record(team),
        starters=starters,
        bench=bench,
    )
    if scoring == "categories":
        wins = getattr(box, f"{side}_wins", 0)
        losses = getattr(box, f"{side}_losses", 0)
        ties = getattr(box, f"{side}_ties", 0)
        tw.score = float(wins)
        tw.category_record = f"{wins}-{losses}-{ties}"
        stats = getattr(box, f"{side}_stats", {}) or {}
        tw.categories_won = sorted(k for k, v in stats.items() if v.get("result") == "WIN")
        tw.categories_lost = sorted(k for k, v in stats.items() if v.get("result") == "LOSS")
    else:
        tw.score = round(float(getattr(box, f"{side}_score", 0) or 0), 2)
        projected = getattr(box, f"{side}_projected", None)
        tw.projected = projected if projected is not None and projected >= 0 else None
    return tw


def summarize_box_scores(
    box_scores: Iterable[Any],
    league_name: str,
    season: int,
    week: int,
    scoring_type: Optional[str] = None,
) -> WeekSummary:
    box_scores = list(box_scores)
    scoring = _scoring_kind(scoring_type, box_scores)
    summary = WeekSummary(league_name=league_name, season=season, week=week, scoring=scoring)
    for box in box_scores:
        home = _team_week(box, "home", scoring)
        away = _team_week(box, "away", scoring)
        if home is None and away is None:
            continue
        if home is None:  # put the bye team in the "home" slot
            home, away = away, None
        winner = getattr(box, "winner", "UNDECIDED")
        status = "final" if winner in ("HOME", "AWAY", "TIE") else "in_progress"
        summary.matchups.append(Matchup(home=home, away=away, status=status, scoring=scoring))
    return summary
