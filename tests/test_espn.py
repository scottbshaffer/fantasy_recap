from types import SimpleNamespace

import pytest

from fantasy_recap.config import EspnConfig
from fantasy_recap.espn import EspnError, fetch_week, summarize_box_scores


def test_points_summary(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    assert s.scoring == "points"
    assert len(s.matchups) == 3
    first = s.matchups[0]
    assert first.status == "final"
    assert first.leader.name == "Dunk Dynasty"
    assert first.margin == 8.5
    assert first.is_close
    # bench and IR players are split from starters; IR doesn't count as bench points
    bricks = first.away
    assert [p.name for p in bricks.starters] == ["Luka Doncic", "Jayson Tatum"]
    assert bricks.bench_points == 155.0
    assert bricks.top_bench_player.name == "Sleeper Pick"


def test_bye_week_and_highlights(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    bye = s.matchups[2]
    assert bye.is_bye and bye.home.name == "Pick and Roll"
    assert s.closest.home.name == "Dunk Dynasty"
    assert s.biggest_blowout.home.name == "Hoop Dreams"
    assert s.high_score_team.name == "Hoop Dreams"
    player, team = s.top_player
    assert (player.name, team.name) == ("Victor Wembanyama", "Hoop Dreams")
    assert s.most_bench_points.name == "Brick Layers"


def test_category_summary(category_box_scores):
    s = summarize_box_scores(category_box_scores, "Cats", 2027, 2, "H2H_CATEGORY")
    m = s.matchups[0]
    assert s.scoring == "categories"
    assert m.status == "in_progress"
    assert m.home.category_record == "5-4-0"
    assert "PTS" in m.home.categories_won and "FT%" in m.home.categories_lost
    assert m.is_close
    d = s.to_dict()
    assert "bench_points_left_on_bench" not in d["matchups"][0]["home"]


def test_scoring_detected_without_settings(category_box_scores):
    s = summarize_box_scores(category_box_scores, "Cats", 2027, 2, None)
    assert s.scoring == "categories"


def test_to_dict_is_json_ready(points_box_scores):
    import json

    d = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS").to_dict()
    json.dumps(d)
    assert d["highlights"]["top_player"]["name"] == "Victor Wembanyama"
    assert d["matchups"][2]["bye"]["team"] == "Pick and Roll"


class FakeLeague:
    def __init__(self, box_scores, current=5):
        self.currentMatchupPeriod = current
        self.settings = SimpleNamespace(name="Fake League", scoring_type="H2H_POINTS")
        self._box = box_scores
        self.calls = []

    def box_scores(self, matchup_period=None, matchup_total=True):
        self.calls.append((matchup_period, matchup_total))
        return self._box


def test_fetch_week_defaults_to_current(points_box_scores):
    league = FakeLeague(points_box_scores)
    s = fetch_week(EspnConfig(league_id=1, year=2027), league=league)
    assert league.calls == [(5, True)]
    assert s.week == 5 and s.league_name == "Fake League"


def test_fetch_week_rejects_future_week(points_box_scores):
    with pytest.raises(EspnError):
        fetch_week(EspnConfig(league_id=1, year=2027), week=9, league=FakeLeague(points_box_scores))
