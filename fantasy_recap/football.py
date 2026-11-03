"""Football's Game of the Week: the best individual stat lines of the week.

An NFL fantasy week is a single game per player, and espn-api's football box scores already carry
each player's stat breakdown for the week, so no extra ESPN requests are needed.
"""

from __future__ import annotations

from typing import Any, Iterable

from .games import rank_big_games
from .models import BigGame

INACTIVE_SLOTS = ("BE", "IR")

# espn-api breakdown names -> the stat keys BigGame uses for football
BREAKDOWN_KEYS = {
    "passingYards": "PASS_YDS",
    "passingTouchdowns": "PASS_TD",
    "passingInterceptions": "INT",
    "rushingYards": "RUSH_YDS",
    "rushingTouchdowns": "RUSH_TD",
    "receivingReceptions": "REC",
    "receivingYards": "REC_YDS",
    "receivingTouchdowns": "REC_TD",
    "defensiveSacks": "SACK",
    "defensiveInterceptions": "DEF_INT",
    "defensiveFumbles": "FUM_REC",
    "defensivePlusSpecialTeamsTouchdowns": "DEF_TD",
    "madeFieldGoals": "FG",
    "madeExtraPoints": "XP",
}


def football_stats(breakdown: dict) -> dict:
    stats = {}
    for name, key in BREAKDOWN_KEYS.items():
        value = breakdown.get(name)
        if value:
            stats[key] = int(value) if float(value).is_integer() else round(value, 1)
    return stats


def notable(stats: dict) -> list[str]:
    g = stats.get
    notes = []
    if g("PASS_YDS", 0) >= 400:
        notes.append("400-yard passer")
    elif g("PASS_YDS", 0) >= 300:
        notes.append("300-yard passer")
    if g("PASS_TD", 0) >= 4:
        notes.append(f"{g('PASS_TD')} TD passes")
    if g("RUSH_YDS", 0) >= 100:
        notes.append("100-yard rusher")
    if g("REC_YDS", 0) >= 100:
        notes.append("100-yard receiver")
    if g("REC", 0) >= 10:
        notes.append(f"{g('REC')} catches")
    scrimmage_tds = g("RUSH_TD", 0) + g("REC_TD", 0)
    if scrimmage_tds >= 3:
        notes.append(f"{scrimmage_tds} touchdowns")
    if g("DEF_TD", 0):
        notes.append("defensive TD")
    if g("SACK", 0) >= 5:
        notes.append(f"{g('SACK'):g} sacks")
    if g("FG", 0) >= 4:
        notes.append(f"{g('FG')} field goals")
    return notes


def big_games_from_box_scores(box_scores: Iterable[Any], week: int, limit: int = 10) -> list[BigGame]:
    """The week's best fantasy outings by rostered players, from espn-api football box scores."""
    games: list[BigGame] = []
    for box in box_scores:
        for side in ("home", "away"):
            team = getattr(box, f"{side}_team", None)
            if not team or isinstance(team, int):
                continue
            for p in getattr(box, f"{side}_lineup", None) or []:
                if getattr(p, "on_bye_week", False):
                    continue
                points = round(float(getattr(p, "points", 0) or 0), 2)
                stats = football_stats(getattr(p, "breakdown", None) or {})
                if not stats and not points:
                    continue  # didn't play
                slot = getattr(p, "slot_position", "") or ""
                opponent = getattr(p, "pro_opponent", "") or ""
                when = getattr(p, "game_date", None)
                games.append(BigGame(
                    player=getattr(p, "name", "Unknown"),
                    fantasy_team=getattr(team, "team_name", "Unknown"),
                    started=slot not in INACTIVE_SLOTS,
                    slot=slot,
                    scoring_period=week,
                    game_score=points,
                    stats=stats,
                    fantasy_points=points,
                    pro_team=getattr(p, "proTeam", "") or "",
                    opponent="" if opponent == "None" else opponent,
                    date=when.strftime("%Y-%m-%d") if hasattr(when, "strftime") else "",
                    notes=notable(stats),
                    sport="football",
                ))
    return rank_big_games(games, limit)
