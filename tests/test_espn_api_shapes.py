"""Build real espn-api box score objects from ESPN-shaped JSON to make sure the
attribute names we read still match the library."""

import pytest

pytest.importorskip("espn_api")

from espn_api.basketball.box_score import H2HPointsBoxScore  # noqa: E402

from fantasy_recap.espn import summarize_box_scores  # noqa: E402
from fantasy_recap.models import TeamWeek  # noqa: E402


def entry(pid, name, slot_id, points):
    return {
        "lineupSlotId": slot_id,
        "playerPoolEntry": {
            "player": {
                "id": pid,
                "fullName": name,
                "defaultPositionId": 1,
                "eligibleSlots": [0, 12],
                "proTeamId": 13,
                "injuryStatus": "ACTIVE",
                "stats": [{"seasonId": 2027, "id": "002027", "scoringPeriodId": 0,
                           "appliedTotal": points, "appliedStats": {}}],
            }
        },
    }


def side(team_id, total, entries):
    return {"teamId": team_id, "totalPointsLive": total, "totalProjectedPointsLive": total + 50,
            "rosterForMatchupPeriod": {"entries": entries}}


class T:
    def __init__(self, tid, name):
        self.team_id, self.team_name, self.team_abbrev = tid, name, name[:3]
        self.wins, self.losses, self.ties = 1, 1, 0


def test_real_points_box_score():
    data = {
        "winner": "AWAY",
        "home": side(1, 500.0, [entry(1, "Starter One", 0, 120.0), entry(2, "Bench One", 12, 60.0)]),
        "away": side(2, 640.0, [entry(3, "Starter Two", 4, 200.0)]),
    }
    box = H2HPointsBoxScore(data, pro_schedule={}, by_matchup=True, year=2027, scoring_period=20)
    box.home_team, box.away_team = T(1, "Home Team"), T(2, "Away Team")

    s = summarize_box_scores([box], "Real Shapes", 2027, 4, "H2H_POINTS")
    m = s.matchups[0]
    assert m.status == "final"
    assert m.leader.name == "Away Team"
    assert m.home.projected == 550.0
    assert isinstance(m.home, TeamWeek)
    assert [p.name for p in m.home.starters] == ["Starter One"]
    assert m.home.bench_points == 60.0
