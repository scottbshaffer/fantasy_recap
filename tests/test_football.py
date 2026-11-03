"""Football support, fed with fakes shaped like espn-api's football objects."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from fantasy_recap.config import EspnConfig
from fantasy_recap.espn import fetch_week, summarize_box_scores
from fantasy_recap.football import big_games_from_box_scores, notable
from fantasy_recap.pdf import render_pdf
from fantasy_recap.story import system_prompt, template_story
from tests.conftest import team


def fb_player(name, slot, points, breakdown=None, opp="KC", bye=False):
    return SimpleNamespace(name=name, slot_position=slot, points=points, proTeam="BUF", injuryStatus="ACTIVE",
                           breakdown=breakdown or {}, pro_opponent=opp, on_bye_week=bye,
                           game_date=datetime(2026, 11, 1, 13, 0))


def fb_box(home, away, hs, as_, hl, al):
    # football box scores have no `winner`
    return SimpleNamespace(home_team=home, away_team=away, home_score=hs, away_score=as_,
                           home_projected=-1, away_projected=-1, home_lineup=hl, away_lineup=al)


@pytest.fixture
def football_box_scores():
    gridiron = team(1, "Gridiron Gang", 5, 3)
    fumble = team(2, "Fumble Squad", 4, 4)
    return [fb_box(
        gridiron, fumble, 131.4, 98.2,
        [fb_player("Josh Allen", "QB", 34.5, {"passingYards": 312, "passingTouchdowns": 3, "passingInterceptions": 1,
                                             "rushingYards": 41, "rushingTouchdowns": 1}),
         fb_player("Bijan Robinson", "RB", 28.1, {"rushingYards": 121, "rushingTouchdowns": 1,
                                                  "receivingReceptions": 4, "receivingYards": 40}),
         fb_player("Bye Guy", "BE", 0, bye=True)],
        [fb_player("Ja'Marr Chase", "WR", 31.0, {"receivingReceptions": 10, "receivingYards": 150,
                                                 "receivingTouchdowns": 2}),
         fb_player("Bad Kicker", "K", -1.0, {"madeFieldGoals": 0}),
         fb_player("Benchwarmer", "BE", 22.0, {"rushingYards": 90, "rushingTouchdowns": 2})],
    )]


def test_big_games_from_box_scores(football_box_scores):
    games = big_games_from_box_scores(football_box_scores, week=9)
    assert [g.player for g in games[:3]] == ["Josh Allen", "Ja'Marr Chase", "Bijan Robinson"]
    allen = games[0]
    assert allen.line() == "312 pass yds, 3 pass TD, 1 INT, 41 rush yds, 1 rush TD"
    assert allen.notes == ["300-yard passer"] and allen.opponent == "KC" and allen.date == "2026-11-01"
    assert games[1].line() == "10 rec, 150 rec yds, 2 rec TD"
    assert "Bye Guy" not in [g.player for g in games]
    bench = next(g for g in games if g.player == "Benchwarmer")
    assert not bench.started


def test_football_notes():
    assert notable({"RUSH_YDS": 104, "RUSH_TD": 2, "REC_TD": 1, "DEF_TD": 0}) == ["100-yard rusher", "3 touchdowns"]


def test_football_summary_uses_default_status(football_box_scores):
    s = summarize_box_scores(football_box_scores, "NFL League", 2026, 9, sport="football", default_status="final")
    assert s.sport == "football" and s.scoring == "points"
    assert s.real_matchups[0].status == "final"
    assert s.real_matchups[0].bench_cost_it is False  # 22 on the bench, lost by 33.2


def test_football_prompt_and_template(football_box_scores):
    prompt = system_prompt("football")
    assert "fantasy football league" in prompt and "category" not in prompt
    s = summarize_box_scores(football_box_scores, "NFL League", 2026, 9, sport="football", default_status="final")
    s.big_games = big_games_from_box_scores(football_box_scores, week=9)
    story = template_story(s)
    assert "Josh Allen, who went for 312 pass yds" in story
    assert "Over the whole week" not in story


class FakeFootballLeague:
    currentMatchupPeriod = 10
    current_week = 10
    teams = []
    settings = SimpleNamespace(name="NFL League", scoring_type="H2H_POINTS")

    def __init__(self, boxes, schedule):
        self.boxes, self.calls = boxes, []
        self.espn_request = SimpleNamespace(league_get=lambda params=None, headers=None: {"schedule": schedule})

    def box_scores(self, week=None):
        self.calls.append(week)
        return self.boxes


def test_fetch_week_football(football_box_scores):
    schedule = [{"matchupPeriodId": 9, "winner": "HOME",
                 "home": {"teamId": 1, "totalPoints": 131.4}, "away": {"teamId": 2, "totalPoints": 98.2}}]
    league = FakeFootballLeague(football_box_scores, schedule)
    config = EspnConfig(league_id=1, year=2026, sport="football")
    s = fetch_week(config, week=9, league=league, big_games=5)
    assert league.calls == [9]
    assert s.sport == "football" and s.real_matchups[0].status == "final"
    assert s.big_games[0].player == "Josh Allen" and len(s.big_games) == 5
    gang = s.real_matchups[0].home
    assert gang.record == "1-0-0" and gang.prev_record == (0, 0, 0, 0.0)


def test_football_pdf(tmp_path, football_box_scores):
    s = summarize_box_scores(football_box_scores, "NFL League \U0001F3C8", 2026, 9, sport="football",
                             default_status="final")
    s.big_games = big_games_from_box_scores(football_box_scores, week=9)
    path = render_pdf(s, "## Headline\nAllen cooked.\n\nPull quote: Allen cooked.", tmp_path / "nfl.pdf")
    assert path.stat().st_size > 10_000
