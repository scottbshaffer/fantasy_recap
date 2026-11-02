from datetime import date

import pytest

from fantasy_recap import cli
from fantasy_recap.config import DEFAULT_MODEL, EspnConfig, StoryConfig, default_season
from fantasy_recap.espn import summarize_box_scores


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("ESPN_LEAGUE_ID", "ESPN_YEAR", "ESPN_S2", "ESPN_SWID", "ANTHROPIC_API_KEY", "FANTASY_RECAP_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "load_dotenv_if_present", lambda: None)


def test_default_season():
    assert default_season(date(2026, 11, 2)) == 2027
    assert default_season(date(2027, 3, 1)) == 2027
    assert default_season(date(2026, 7, 1)) == 2026


def test_espn_config_from_env(monkeypatch):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "12345")
    monkeypatch.setenv("ESPN_YEAR", "2027")
    monkeypatch.setenv("ESPN_S2", "s2cookie")
    monkeypatch.setenv("ESPN_SWID", "ABC-DEF")
    c = EspnConfig.from_env()
    assert (c.league_id, c.year, c.espn_s2, c.swid) == (12345, 2027, "s2cookie", "{ABC-DEF}")


def test_espn_config_requires_league_id():
    with pytest.raises(ValueError):
        EspnConfig.from_env()


def test_story_config_model(monkeypatch):
    assert StoryConfig.from_env().model == DEFAULT_MODEL
    monkeypatch.setenv("FANTASY_RECAP_MODEL", "claude-opus-5-5")
    assert StoryConfig.from_env().model == "claude-opus-5-5"
    assert StoryConfig.from_env(model="x").model == "x"


def test_cli_prints_story_and_writes_file(monkeypatch, tmp_path, capsys, points_box_scores):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "1")
    seen = {}

    def fake_fetch(config, week=None, previous=False, big_games=10):
        seen["week"] = week
        return summarize_box_scores(points_box_scores, "Test League", config.year, week or 3, "H2H_POINTS")

    monkeypatch.setattr(cli, "fetch_week", fake_fetch)
    out = tmp_path / "recaps" / "week3.txt"
    assert cli.main(["--week", "3", "--out", str(out)]) == 0
    printed = capsys.readouterr()
    assert seen["week"] == 3
    assert "Victor Wembanyama" in printed.out
    assert "ANTHROPIC_API_KEY" in printed.err
    assert out.read_text().strip() == printed.out.strip()


def test_cli_json(monkeypatch, capsys, points_box_scores):
    import json

    monkeypatch.setenv("ESPN_LEAGUE_ID", "1")
    monkeypatch.setattr(cli, "fetch_week", lambda c, week=None, previous=False, big_games=10: summarize_box_scores(
        points_box_scores, "Test League", 2027, 3, "H2H_POINTS"))
    assert cli.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["week"] == 3


def test_cli_missing_league_id(capsys):
    assert cli.main([]) == 2
    assert "ESPN_LEAGUE_ID" in capsys.readouterr().err


def test_cli_export(monkeypatch, tmp_path, capsys, points_box_scores):
    import json

    monkeypatch.setenv("ESPN_LEAGUE_ID", "1")
    seen = {}

    def fake_fetch(config, week=None, previous=False, big_games=10):
        seen.update(week=week, previous=previous, big_games=big_games)
        return summarize_box_scores(points_box_scores, "Test League", 2027, 7, "H2H_POINTS")

    monkeypatch.setattr(cli, "fetch_week", fake_fetch)
    assert cli.main(["--previous", "--export", str(tmp_path / "out")]) == 0
    path = tmp_path / "out" / "2027-week-07.json"
    assert capsys.readouterr().out.strip() == str(path)
    assert seen == {"week": None, "previous": True, "big_games": 10}
    assert json.loads(path.read_text())["week"] == 7


def test_cli_week_and_previous_conflict():
    with pytest.raises(SystemExit):
        cli.main(["--week", "2", "--previous"])
