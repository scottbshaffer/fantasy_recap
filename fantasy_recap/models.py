"""Plain data structures describing one week of fantasy matchups.

These are deliberately independent of the espn-api objects so the story
writer and tests never need network access or ESPN classes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class PlayerLine:
    name: str
    slot: str  # lineup slot, e.g. "PG", "UT", "BE" (bench), "IR"
    points: float = 0.0
    pro_team: str = ""
    injury_status: str = ""

    @property
    def on_bench(self) -> bool:
        return self.slot in ("BE", "IR")


@dataclass
class TeamWeek:
    name: str
    abbrev: str = ""
    record: str = ""  # season record going into / during the week, e.g. "3-1-0"
    score: float = 0.0  # points (points leagues) or category wins (category leagues)
    projected: Optional[float] = None
    category_record: Optional[str] = None  # "6-3-0" in category leagues
    categories_won: list[str] = field(default_factory=list)
    categories_lost: list[str] = field(default_factory=list)
    starters: list[PlayerLine] = field(default_factory=list)
    bench: list[PlayerLine] = field(default_factory=list)

    @property
    def bench_points(self) -> float:
        return round(sum(p.points for p in self.bench if p.slot == "BE"), 2)

    @property
    def top_performer(self) -> Optional[PlayerLine]:
        players = self.starters or self.bench
        return max(players, key=lambda p: p.points) if players else None

    @property
    def top_bench_player(self) -> Optional[PlayerLine]:
        bench = [p for p in self.bench if p.slot == "BE"]
        return max(bench, key=lambda p: p.points) if bench else None


@dataclass
class Matchup:
    home: TeamWeek
    away: Optional[TeamWeek]  # None for a bye week
    status: str = "in_progress"  # "final" or "in_progress"
    scoring: str = "points"  # "points" or "categories"

    @property
    def is_bye(self) -> bool:
        return self.away is None

    @property
    def margin(self) -> float:
        if self.away is None:
            return 0.0
        return round(abs(self.home.score - self.away.score), 2)

    @property
    def leader(self) -> Optional[TeamWeek]:
        if self.away is None or self.home.score == self.away.score:
            return None
        return self.home if self.home.score > self.away.score else self.away

    @property
    def trailer(self) -> Optional[TeamWeek]:
        leader = self.leader
        if leader is None:
            return None
        return self.away if leader is self.home else self.home

    @property
    def is_close(self) -> bool:
        if self.away is None:
            return False
        if self.scoring == "categories":
            return self.margin <= 1
        total = self.home.score + self.away.score
        return total > 0 and self.margin / total <= 0.03


@dataclass
class WeekSummary:
    league_name: str
    season: int
    week: int  # ESPN matchup period
    scoring: str  # "points" or "categories"
    matchups: list[Matchup] = field(default_factory=list)

    @property
    def real_matchups(self) -> list[Matchup]:
        return [m for m in self.matchups if not m.is_bye]

    @property
    def closest(self) -> Optional[Matchup]:
        games = self.real_matchups
        return min(games, key=lambda m: m.margin) if games else None

    @property
    def biggest_blowout(self) -> Optional[Matchup]:
        games = self.real_matchups
        return max(games, key=lambda m: m.margin) if games else None

    @property
    def high_score_team(self) -> Optional[TeamWeek]:
        teams = [t for m in self.matchups for t in (m.home, m.away) if t]
        return max(teams, key=lambda t: t.score) if teams else None

    @property
    def top_player(self) -> Optional[tuple[PlayerLine, TeamWeek]]:
        best = None
        for m in self.matchups:
            for team in (m.home, m.away):
                if not team:
                    continue
                for p in team.starters:
                    if best is None or p.points > best[0].points:
                        best = (p, team)
        return best

    @property
    def most_bench_points(self) -> Optional[TeamWeek]:
        teams = [t for m in self.matchups for t in (m.home, m.away) if t]
        if not teams:
            return None
        team = max(teams, key=lambda t: t.bench_points)
        return team if team.bench_points > 0 else None

    def to_dict(self) -> dict:
        """A compact, JSON-friendly view used as the prompt for the story writer."""

        def team_view(t: TeamWeek) -> dict:
            top = t.top_performer
            bench_top = t.top_bench_player
            view = {
                "team": t.name,
                "season_record": t.record,
                "score": t.score,
            }
            if t.projected is not None and t.projected >= 0:
                view["projected"] = t.projected
            if t.category_record:
                view["category_record"] = t.category_record
                view["categories_won"] = t.categories_won
                view["categories_lost"] = t.categories_lost
            if self.scoring == "categories":
                # ESPN doesn't report per-player fantasy points in category leagues.
                view["starters"] = [{"name": p.name, "slot": p.slot} for p in t.starters]
            else:
                if top:
                    view["top_performer"] = {"name": top.name, "points": top.points}
                view["starters"] = [
                    {"name": p.name, "slot": p.slot, "points": p.points}
                    for p in sorted(t.starters, key=lambda p: -p.points)
                ]
            if self.scoring == "points":
                view["bench_points_left_on_bench"] = t.bench_points
                if bench_top and bench_top.points > 0:
                    view["best_bench_player"] = {
                        "name": bench_top.name,
                        "points": bench_top.points,
                    }
            return view

        matchups = []
        for m in self.matchups:
            if m.is_bye:
                matchups.append({"bye": team_view(m.home)})
                continue
            leader = m.leader
            matchups.append(
                {
                    "status": m.status,
                    "home": team_view(m.home),
                    "away": team_view(m.away),
                    "leader": leader.name if leader else "tied",
                    "margin": m.margin,
                    "close_game": m.is_close,
                }
            )

        highlights = {}
        if self.closest:
            highlights["closest_matchup"] = _pair(self.closest)
        if self.biggest_blowout:
            highlights["biggest_blowout"] = _pair(self.biggest_blowout)
        if self.high_score_team:
            highlights["high_score_team"] = self.high_score_team.name
        if self.scoring == "points" and self.top_player:
            p, t = self.top_player
            highlights["top_player"] = {"name": p.name, "points": p.points, "team": t.name}
        if self.scoring == "points" and self.most_bench_points:
            t = self.most_bench_points
            highlights["most_points_left_on_bench"] = {"team": t.name, "points": t.bench_points}

        return {
            "league": self.league_name,
            "season": self.season,
            "week": self.week,
            "scoring": self.scoring,
            "matchups": matchups,
            "highlights": highlights,
        }


def _pair(m: Matchup) -> dict:
    return {"teams": [m.home.name, m.away.name], "margin": m.margin}


__all__ = ["PlayerLine", "TeamWeek", "Matchup", "WeekSummary", "asdict"]
