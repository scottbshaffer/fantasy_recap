import json
from types import SimpleNamespace

from fantasy_recap.config import EspnConfig
from fantasy_recap.espn import export_summary, fetch_week, summarize_box_scores
from fantasy_recap.games import game_score, notable, parse_scoring_period, rank_big_games, scoring_periods_for
from fantasy_recap.models import BigGame
from fantasy_recap.story import template_story

# ESPN stat ids: 0 PTS, 1 BLK, 2 STL, 3 AST, 6 REB, 11 TO, 13 FGM, 14 FGA, 17 3PM, 40 MIN


def entry(name, slot_id, day, stats, applied, pro_team_id=7):
    return {
        "lineupSlotId": slot_id,
        "playerPoolEntry": {"player": {
            "fullName": name,
            "proTeamId": pro_team_id,
            "stats": [
                {"scoringPeriodId": day, "statSourceId": 1, "stats": {"0": 99}, "appliedTotal": 999},  # projection
                {"scoringPeriodId": day, "statSourceId": 0, "stats": stats, "appliedTotal": applied},
            ],
        }},
    }


def day_data(day):
    return {"schedule": [{
        "home": {"teamId": 1, "rosterForCurrentScoringPeriod": {"entries": [
            entry("Nikola Jokic", 4, day, {"0": 31, "6": 14, "3": 12, "2": 2, "1": 1, "11": 3, "40": 36}, 78.5),
            entry("DNP Guy", 0, day, {"0": 0, "40": 0}, 0),
        ]}},
        "away": {"teamId": 2, "rosterForCurrentScoringPeriod": {"entries": [
            entry("Bench Bomber", 12, day, {"0": 44, "6": 5, "3": 4, "17": 8, "40": 38}, 61.0, pro_team_id=2),
        ]}},
    }]}


def test_parse_scoring_period_reads_actual_box_scores():
    pro_schedule = {7: {"40": [{"awayProTeamId": 7, "homeProTeamId": 2, "date": 1762300800000}]}}
    games = parse_scoring_period(day_data(40), 40, {1: "Dunk Dynasty", 2: "Brick Layers"}, pro_schedule)
    by_name = {g.player: g for g in games}
    assert set(by_name) == {"Nikola Jokic", "Bench Bomber"}  # DNP is skipped
    jokic = by_name["Nikola Jokic"]
    assert jokic.started and jokic.slot == "C"
    assert jokic.stats["PTS"] == 31 and jokic.fantasy_points == 78.5 and jokic.game_score == 78.5
    assert jokic.notes == ["triple-double"]
    assert jokic.fantasy_team == "Dunk Dynasty"
    assert jokic.opponent == "BOS" and jokic.date == "2025-11-04"
    bomber = by_name["Bench Bomber"]
    assert not bomber.started and bomber.slot == "BE"
    assert "40-point game" in bomber.notes and "8+ threes" in bomber.notes
    assert jokic.line() == "31 pts, 14 reb, 12 ast"


def test_category_leagues_use_game_score():
    games = parse_scoring_period(day_data(40), 40, {}, use_fantasy_points=False)
    jokic = next(g for g in games if g.player == "Nikola Jokic")
    assert jokic.fantasy_points is None
    assert jokic.game_score == game_score(jokic.stats)


def test_notable_thresholds():
    assert notable({"PTS": 52}) == ["50-point game"]
    assert notable({"PTS": 20, "REB": 12}) == ["double-double"]


def test_rank_and_days():
    games = [BigGame("A", "T", True, "PG", 1, 10), BigGame("B", "T", True, "PG", 1, 30)]
    assert [g.player for g in rank_big_games(games, 1)] == ["B"]
    league = SimpleNamespace(matchup_ids={3: ["15", "14", "16", "17"]}, current_week=16)
    assert scoring_periods_for(league, 3) == [14, 15, 16]


class DayLeague:
    """A fake league whose ESPN requests return one day of raw data."""

    def __init__(self, box_scores):
        self.currentMatchupPeriod = 4
        self.current_week = 41
        self.matchup_ids = {3: ["40"], 4: ["41"]}
        self.settings = SimpleNamespace(name="Fake League", scoring_type="H2H_POINTS")
        self.teams = [SimpleNamespace(team_id=1, team_name="Dunk Dynasty"), SimpleNamespace(team_id=2, team_name="Brick Layers")]
        self.pro_schedule = {}
        self._box = box_scores
        self.requests = []
        self.espn_request = SimpleNamespace(league_get=self._get)

    def _get(self, params, headers):
        self.requests.append(params["scoringPeriodId"])
        return day_data(params["scoringPeriodId"])

    def box_scores(self, matchup_period=None, matchup_total=True):
        self.week = matchup_period
        return self._box


def test_fetch_week_previous_includes_big_games(points_box_scores, tmp_path):
    league = DayLeague(points_box_scores)
    s = fetch_week(EspnConfig(league_id=1, year=2027), league=league, previous=True)
    assert s.week == 3 and league.week == 3 and league.requests == [40]
    assert s.big_games[0].player == "Nikola Jokic"

    story = template_story(s)
    assert "The game of the week belonged to Nikola Jokic, who went for 31 pts, 14 reb, 12 ast (triple-double)" in story
    assert "Bench Bomber's 44 pts, 5 reb, 4 ast, 8 3pm night was wasted on Brick Layers' bench" in story
    from fantasy_recap.story import parse_story

    assert len(parse_story(story).sections) <= 3

    path = export_summary(s, tmp_path / "summaries")
    assert path.name == "2027-week-03.json"
    data = json.loads(path.read_text())
    assert data["status"] == "final"  # byes are ignored; both real games are final
    assert data["big_games"][1]["player"] == "Bench Bomber"
    assert data["big_games"][1]["started"] is False
    assert data["big_games"][0]["box_score"] == "31 pts, 14 reb, 12 ast"


def test_big_game_failure_does_not_break_recap(points_box_scores):
    league = DayLeague(points_box_scores)
    league.espn_request = SimpleNamespace(league_get=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    s = fetch_week(EspnConfig(league_id=1, year=2027), league=league, week=3)
    assert s.big_games == [] and len(s.matchups) == 3


def test_big_games_can_be_skipped(points_box_scores):
    league = DayLeague(points_box_scores)
    fetch_week(EspnConfig(league_id=1, year=2027), league=league, week=3, big_games=0)
    assert league.requests == []


def test_summary_without_big_games_has_no_key(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    assert "big_games" not in s.to_dict()
