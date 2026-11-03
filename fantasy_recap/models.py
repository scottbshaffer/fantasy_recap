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
class BigGame:
    """One player's single-game box score from the week."""

    player: str
    fantasy_team: str
    started: bool  # in an active lineup slot that day (False = bench or IR)
    slot: str
    scoring_period: int
    game_score: float  # fantasy points in points leagues, a standard game score otherwise
    stats: dict = field(default_factory=dict)  # PTS, REB, AST, STL, BLK, 3PM, TO, FGM, FGA, MIN...
    fantasy_points: Optional[float] = None
    pro_team: str = ""
    opponent: str = ""
    date: str = ""  # YYYY-MM-DD, US Eastern
    notes: list[str] = field(default_factory=list)  # e.g. "triple-double", "40-point game"

    def line(self) -> str:
        """Short box score line, e.g. '42 pts, 12 reb, 10 ast'."""
        parts = []
        for key, label in (("PTS", "pts"), ("REB", "reb"), ("AST", "ast"), ("STL", "stl"),
                           ("BLK", "blk"), ("3PM", "3pm")):
            value = self.stats.get(key)
            if value is None:
                continue
            if key in ("PTS", "REB", "AST") or value >= 3:
                parts.append(f"{int(value)} {label}")
        return ", ".join(parts)

    def to_dict(self) -> dict:
        d = {
            "player": self.player,
            "fantasy_team": self.fantasy_team,
            "started": self.started,
            "slot": self.slot,
            "date": self.date,
            "pro_team": self.pro_team,
            "opponent": self.opponent,
            "box_score": self.line(),
            "stats": self.stats,
            "game_score": self.game_score,
            "notes": self.notes,
        }
        if self.fantasy_points is not None:
            d["fantasy_points"] = self.fantasy_points
        return {k: v for k, v in d.items() if v not in ("", None)}


@dataclass
class TeamWeek:
    name: str
    abbrev: str = ""
    record: str = ""  # season record through this week, e.g. "3-1-0"
    team_id: Optional[int] = None
    logo_url: str = ""
    wins: int = 0  # season totals through this week
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    prev_record: Optional[tuple[int, int, int, float]] = None  # (wins, losses, ties, points for) before this week
    score: float = 0.0  # points (points leagues) or category wins (category leagues)
    projected: Optional[float] = None
    category_record: Optional[str] = None  # "6-3-0" in category leagues
    categories_won: list[str] = field(default_factory=list)
    categories_lost: list[str] = field(default_factory=list)
    starters: list[PlayerLine] = field(default_factory=list)
    bench: list[PlayerLine] = field(default_factory=list)

    @property
    def key(self):
        """Stable identity for this team across weeks (ESPN team id, else the name)."""
        return self.team_id if self.team_id is not None else self.name

    @property
    def short_record(self) -> str:
        """'2-0', or '2-0-1' when there are ties; '' when unknown."""
        if not self.record:
            return ""
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")

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
    def is_upset(self) -> bool:
        """A finished game won by the team with the worse record going into the week."""
        if self.status != "final" or self.leader is None:
            return False
        before = [t.prev_record for t in (self.leader, self.trailer)]
        if any(r is None or sum(r[:3]) == 0 for r in before):
            return False
        return _win_pct(before[0]) < _win_pct(before[1])

    @property
    def bench_cost_it(self) -> bool:
        """The loser's bench outscored the margin (points leagues only)."""
        if self.scoring != "points" or self.status != "final" or self.trailer is None:
            return False
        return self.trailer.bench_points > self.margin

    @property
    def is_close(self) -> bool:
        if self.away is None:
            return False
        if self.scoring == "categories":
            return self.margin <= 1
        total = self.home.score + self.away.score
        return total > 0 and self.margin / total <= 0.03


def _win_pct(rec: tuple) -> float:
    wins, losses, ties = rec[:3]
    games = wins + losses + ties
    return (wins + ties / 2) / games if games else 0.0


@dataclass
class Standing:
    team: TeamWeek
    rank: int
    prev_rank: Optional[int] = None  # None when there was no earlier week to compare with

    @property
    def movement(self) -> int:
        """Places gained since last week (negative = dropped)."""
        return 0 if self.prev_rank is None else self.prev_rank - self.rank


def _rank(teams: list[TeamWeek], rec) -> dict:
    ordered = sorted(teams, key=lambda t: (-_win_pct(rec(t)), -rec(t)[3], t.name.lower()))
    return {id(t): i + 1 for i, t in enumerate(ordered)}


@dataclass
class WeekSummary:
    league_name: str
    season: int
    week: int  # ESPN matchup period
    scoring: str  # "points" or "categories"
    matchups: list[Matchup] = field(default_factory=list)
    big_games: list[BigGame] = field(default_factory=list)  # best single games, best first

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
    def teams(self) -> list[TeamWeek]:
        return [t for m in self.matchups for t in (m.home, m.away) if t]

    @property
    def standings(self) -> list[Standing]:
        """League table through this week, by win % then points for. Empty without records."""
        teams = self.teams
        if not teams or not any(t.record for t in teams):
            return []
        now = _rank(teams, lambda t: (t.wins, t.losses, t.ties, t.points_for))
        before = None
        if self.week > 1 and all(t.prev_record for t in teams):
            before = _rank(teams, lambda t: t.prev_record)
        rows = [Standing(t, now[id(t)], before[id(t)] if before else None) for t in teams]
        return sorted(rows, key=lambda r: r.rank)

    @property
    def lowest_score_team(self) -> Optional[TeamWeek]:
        teams = [t for m in self.real_matchups for t in (m.home, m.away)]
        return min(teams, key=lambda t: t.score) if teams else None

    @property
    def worst_starter(self) -> Optional[tuple[PlayerLine, TeamWeek]]:
        """The lowest-scoring player in an active lineup slot (points leagues)."""
        worst = None
        for team in self.teams:
            for p in team.starters:
                if worst is None or p.points < worst[0].points:
                    worst = (p, team)
        return worst

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
                    **({"upset": True} if m.is_upset else {}),
                }
            )

        highlights = {}
        if self.closest:
            highlights["closest_matchup"] = _pair(self.closest)
        if self.biggest_blowout:
            highlights["biggest_blowout"] = _pair(self.biggest_blowout)
        if self.high_score_team:
            t = self.high_score_team
            highlights["high_score_team"] = {"team": t.name, "score": t.score}
        if self.scoring == "points" and self.top_player:
            p, t = self.top_player
            highlights["top_player"] = {"name": p.name, "points": p.points, "team": t.name}
        if self.scoring == "points" and self.most_bench_points:
            t = self.most_bench_points
            highlights["most_points_left_on_bench"] = {"team": t.name, "points": t.bench_points}

        upsets = [m.leader.name for m in self.real_matchups if m.is_upset]
        if upsets:
            highlights["upset_winners"] = upsets

        result = {
            "league": self.league_name,
            "season": self.season,
            "week": self.week,
            "scoring": self.scoring,
            "matchups": matchups,
            "highlights": highlights,
        }
        if self.standings:
            result["standings"] = [
                {"rank": r.rank, "team": r.team.name, "record": r.team.record,
                 **({"places_moved": r.movement} if r.prev_rank is not None else {})}
                for r in self.standings
            ]
        if self.big_games:
            result["big_games"] = [g.to_dict() for g in self.big_games]
        return result


def _pair(m: Matchup) -> dict:
    return {"teams": [m.home.name, m.away.name], "margin": m.margin}


__all__ = ["BigGame", "PlayerLine", "TeamWeek", "Matchup", "Standing", "WeekSummary", "asdict"]
