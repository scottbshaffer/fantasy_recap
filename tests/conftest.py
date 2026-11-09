"""Fake ESPN objects shaped like espn-api's, so tests never touch the network."""

from types import SimpleNamespace

import pytest


def player(name, slot, points, pro="LAL"):
    return SimpleNamespace(name=name, slot_position=slot, points=points, proTeam=pro, injuryStatus="ACTIVE")


def team(tid, name, wins=2, losses=1, ties=0):
    return SimpleNamespace(team_id=tid, team_name=name, team_abbrev=name[:4].upper(), wins=wins, losses=losses, ties=ties)


def points_box(home, away, home_score, away_score, home_lineup, away_lineup, winner="UNDECIDED"):
    return SimpleNamespace(
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        home_projected=-1,
        away_projected=-1,
        home_lineup=home_lineup,
        away_lineup=away_lineup,
        winner=winner,
    )


@pytest.fixture
def points_box_scores():
    dunk = team(1, "Dunk Dynasty", 3, 0)
    bricks = team(2, "Brick Layers", 1, 2)
    hoops = team(3, "Hoop Dreams", 2, 1)
    zone = team(4, "Zone Defense", 0, 3)
    pick = team(5, "Pick and Roll", 2, 1)
    return [
        points_box(
            dunk, bricks, 1012.5, 1004.0,
            [player("Nikola Jokic", "C", 312.0, "DEN"), player("Tyrese Haliburton", "PG", 198.5, "IND"),
             player("Bench Guy", "BE", 40.0)],
            [player("Luka Doncic", "PG", 288.0, "LAL"), player("Jayson Tatum", "SF", 240.0, "BOS"),
             player("Sleeper Pick", "BE", 155.0, "OKC"), player("Hurt Star", "IR", 0.0)],
            winner="HOME",
        ),
        points_box(
            hoops, zone, 1201.0, 803.25,
            [player("Victor Wembanyama", "C", 341.0, "SA"), player("Anthony Edwards", "SG", 250.0, "MIN")],
            [player("Role Player", "F", 120.0), player("Another Guy", "UT", 95.25)],
            winner="HOME",
        ),
        # a bye: espn-api leaves the away team as id 0
        SimpleNamespace(home_team=pick, away_team=0, home_score=900.0, away_score=0, home_projected=-1,
                        away_projected=-1, home_lineup=[player("Solo Act", "PG", 210.0)], away_lineup=[],
                        winner="UNDECIDED"),
    ]


@pytest.fixture
def category_box_scores():
    a = team(1, "Stat Stuffers")
    b = team(2, "Punt FT%")

    def stats(results):
        return {cat: {"value": 1.0, "result": r} for cat, r in results.items()}

    return [
        SimpleNamespace(
            home_team=a, away_team=b,
            home_wins=5, home_losses=4, home_ties=0,
            away_wins=4, away_losses=5, away_ties=0,
            home_stats=stats({"PTS": "WIN", "REB": "WIN", "AST": "WIN", "STL": "WIN", "BLK": "WIN",
                              "3PM": "LOSS", "FG%": "LOSS", "FT%": "LOSS", "TO": "LOSS"}),
            away_stats=stats({"PTS": "LOSS", "REB": "LOSS", "AST": "LOSS", "STL": "LOSS", "BLK": "LOSS",
                              "3PM": "WIN", "FG%": "WIN", "FT%": "WIN", "TO": "WIN"}),
            home_lineup=[player("Domantas Sabonis", "C", 0.0, "SAC")],
            away_lineup=[player("Stephen Curry", "PG", 0.0, "GSW")],
            winner="UNDECIDED",
        )
    ]


@pytest.fixture(autouse=True)
def no_story_writers(monkeypatch):
    """Keep tests from finding real keys or a real `claude`/`codex` CLI on this machine."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FANTASY_RECAP_PROVIDER", "FANTASY_RECAP_MODEL",
                "FANTASY_RECAP_BASE_URL", "FANTASY_RECAP_API_KEY", "FANTASY_RECAP_CLAUDE_BIN",
                "FANTASY_RECAP_CODEX_BIN", "FANTASY_RECAP_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("fantasy_recap.providers._which", lambda name: None)
