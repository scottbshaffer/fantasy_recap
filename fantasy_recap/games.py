"""Find the week's biggest single-game performances by rostered players.

ESPN stores each day of an NBA fantasy week as a "scoring period". For every
day in the matchup we ask for that day's rosters, read each player's raw box
score for the day, and keep the best games.

`parse_scoring_period` works on the raw ESPN JSON so it can be tested offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .models import BigGame

try:  # the maps live in espn-api; fall back to ids if it's missing
    from espn_api.basketball.constant import POSITION_MAP, PRO_TEAM_MAP, STATS_MAP
except ImportError:  # pragma: no cover
    POSITION_MAP, PRO_TEAM_MAP, STATS_MAP = {}, {}, {}

BOX_KEYS = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FGM", "FGA", "FTM", "FTA", "MIN")
INACTIVE_SLOTS = ("BE", "IR")
EASTERN = timezone(timedelta(hours=-5))  # close enough for a game date


def game_score(stats: dict) -> float:
    """A simple, ESPN-style default points formula for leagues without fantasy points."""
    g = stats.get
    score = (
        g("PTS", 0) + 1.2 * g("REB", 0) + 1.5 * g("AST", 0) + 3 * g("STL", 0)
        + 3 * g("BLK", 0) + g("3PM", 0) - g("TO", 0)
        + (g("FGM", 0) - g("FGA", 0)) + 0.5 * (g("FTM", 0) - g("FTA", 0))
    )
    return round(score, 2)


def notable(stats: dict) -> list[str]:
    g = stats.get
    notes = []
    tens = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if g(k, 0) >= 10)
    if tens >= 4:
        notes.append("quadruple-double")
    elif tens == 3:
        notes.append("triple-double")
    elif tens == 2:
        notes.append("double-double")
    for key, threshold, label in (
        ("PTS", 50, "50-point game"), ("PTS", 40, "40-point game"), ("REB", 20, "20-rebound game"),
        ("AST", 15, "15-assist game"), ("BLK", 5, "5+ blocks"), ("STL", 5, "5+ steals"), ("3PM", 8, "8+ threes"),
    ):
        if g(key, 0) >= threshold and not (key == "PTS" and threshold == 40 and g(key, 0) >= 50):
            notes.append(label)
    return notes


def _day_split(player: dict, scoring_period: int) -> Optional[dict]:
    candidates = [s for s in player.get("stats", []) if s.get("scoringPeriodId") == scoring_period]
    actual = [s for s in candidates if s.get("statSourceId", 0) == 0]
    return (actual or candidates or [None])[0]


def _game_info(pro_schedule: dict, pro_team_id: Any, scoring_period: int) -> tuple[str, str]:
    games = (pro_schedule or {}).get(pro_team_id, {}).get(str(scoring_period)) or []
    if not games:
        return "", ""
    game = games[0]
    opp_id = game["awayProTeamId"] if game.get("awayProTeamId") != pro_team_id else game.get("homeProTeamId")
    when = ""
    if game.get("date"):
        when = datetime.fromtimestamp(game["date"] / 1000, tz=EASTERN).strftime("%Y-%m-%d")
    return PRO_TEAM_MAP.get(opp_id, str(opp_id or "")), when


def parse_scoring_period(
    data: dict,
    scoring_period: int,
    team_names: dict,
    pro_schedule: Optional[dict] = None,
    use_fantasy_points: bool = True,
) -> list[BigGame]:
    """Turn one day's raw ESPN matchup data into BigGame rows for everyone who played."""
    games: list[BigGame] = []
    for matchup in data.get("schedule", []):
        for side in ("home", "away"):
            team_data = matchup.get(side)
            if not team_data:
                continue
            team_name = team_names.get(team_data.get("teamId"), f"Team {team_data.get('teamId')}")
            roster = team_data.get("rosterForCurrentScoringPeriod", {})
            for entry in roster.get("entries", []):
                pool = entry.get("playerPoolEntry", {})
                player = pool.get("player") or entry.get("player") or {}
                split = _day_split(player, scoring_period)
                if not split:
                    continue
                stats = {STATS_MAP.get(k, k): v for k, v in (split.get("stats") or {}).items()}
                stats = {k: (int(v) if float(v).is_integer() else round(v, 1)) for k, v in stats.items()
                         if k in BOX_KEYS}
                if not (stats.get("MIN", 0) > 0 or stats.get("PTS", 0) or stats.get("REB", 0)):
                    continue  # didn't play
                fantasy_points = split.get("appliedTotal")
                if fantasy_points is not None:
                    fantasy_points = round(fantasy_points, 2)
                slot = POSITION_MAP.get(entry.get("lineupSlotId"), str(entry.get("lineupSlotId", "")))
                pro_id = player.get("proTeamId")
                opponent, when = _game_info(pro_schedule, pro_id, scoring_period)
                score = fantasy_points if (use_fantasy_points and fantasy_points) else game_score(stats)
                games.append(
                    BigGame(
                        player=player.get("fullName", "Unknown"),
                        fantasy_team=team_name,
                        started=slot not in INACTIVE_SLOTS,
                        slot=slot,
                        scoring_period=scoring_period,
                        game_score=score,
                        stats=stats,
                        fantasy_points=fantasy_points if use_fantasy_points else None,
                        pro_team=PRO_TEAM_MAP.get(pro_id, ""),
                        opponent=opponent,
                        date=when,
                        notes=notable(stats),
                    )
                )
    return games


def rank_big_games(games: Iterable[BigGame], limit: int = 10) -> list[BigGame]:
    return sorted(games, key=lambda g: (-g.game_score, g.player))[:limit]


def scoring_periods_for(league: Any, matchup_period: int) -> list[int]:
    """The days (scoring periods) of a matchup period that have been played so far."""
    ids = (getattr(league, "matchup_ids", None) or {}).get(matchup_period, [])
    days = sorted({int(i) for i in ids})
    current = getattr(league, "current_week", None)
    if current is not None:
        days = [d for d in days if d <= current]
    return days


def fetch_big_games(league: Any, matchup_period: int, use_fantasy_points: bool = True, limit: int = 10) -> list[BigGame]:
    team_names = {t.team_id: t.team_name for t in getattr(league, "teams", [])}
    pro_schedule = getattr(league, "pro_schedule", {}) or {}
    headers = {"x-fantasy-filter": json.dumps({"schedule": {"filterMatchupPeriodIds": {"value": [matchup_period]}}})}
    games: list[BigGame] = []
    for day in scoring_periods_for(league, matchup_period):
        params = {"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": day}
        data = league.espn_request.league_get(params=params, headers=headers)
        games.extend(parse_scoring_period(data, day, team_names, pro_schedule, use_fantasy_points))
    return rank_big_games(games, limit)
