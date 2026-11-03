#!/usr/bin/env python3
"""Print a recap for a made-up league, so you can see the output without an ESPN league.

    python scripts/sample_recap.py                  # template story, no API key needed
    python scripts/sample_recap.py --pdf sample.pdf # also render the shareable PDF
    python scripts/sample_recap.py --ai             # have your story writer write it (see the README)

Every team, player and stat here is invented. The README's sample recap comes from this script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_recap.config import StoryConfig, load_dotenv_if_present  # noqa: E402
from fantasy_recap.espn import summarize_box_scores  # noqa: E402
from fantasy_recap.models import BigGame, WeekSummary  # noqa: E402
from fantasy_recap.story import parse_story, team_of_the_week, write_story  # noqa: E402


def _team(tid, name, wins, losses, points_for, prev):
    return SimpleNamespace(team_id=tid, team_name=name, team_abbrev=name[:4].upper(), wins=wins,
                           losses=losses, ties=0, points_for=points_for, prev=prev, logo_url="")


def _player(name, slot, points, pro):
    return SimpleNamespace(name=name, slot_position=slot, points=points, proTeam=pro, injuryStatus="ACTIVE")


def _box(home, away, home_score, away_score, home_lineup, away_lineup):
    return SimpleNamespace(home_team=home, away_team=away, home_score=home_score, away_score=away_score,
                           home_projected=-1, away_projected=-1, home_lineup=home_lineup,
                           away_lineup=away_lineup, winner="HOME" if home_score > away_score else "AWAY")


def sample_summary() -> WeekSummary:
    # (wins, losses, ties, points for) going into the week, so the standings show movement
    dunk = _team(1, "Dunk Dynasty", 3, 0, 3105.5, (2, 0, 0, 2093.0))
    bricks = _team(2, "Brick Layers", 1, 2, 2950.0, (1, 1, 0, 1946.0))
    hoops = _team(3, "Hoop Dreams", 2, 1, 3240.0, (1, 1, 0, 2039.0))
    zone = _team(4, "Zone Defense", 0, 3, 2611.25, (0, 2, 0, 1808.0))
    cereal = _team(5, "Cereal Killers", 2, 1, 2980.0, (2, 0, 0, 2002.5))
    wind = _team(6, "Second Wind", 1, 2, 2890.5, (0, 2, 0, 1903.0))
    boxes = [
        _box(dunk, bricks, 1012.5, 1004.0,
             [_player("Marcus Vale", "C", 312.0, "DEN"), _player("Theo Brandt", "PG", 198.5, "IND"),
              _player("Jules Okafor", "SF", 176.0, "MIA"), _player("Benchwarmer Bob", "BE", 40.0, "UTA")],
             [_player("Andre Castillo", "PG", 288.0, "DAL"), _player("Rafe Lindqvist", "SF", 240.0, "BOS"),
              _player("Kenji Moss", "PF", 151.0, "CHI"), _player("Sleeper Pick", "BE", 155.0, "OKC")]),
        _box(hoops, zone, 1201.0, 803.25,
             [_player("Victor Sol", "C", 341.0, "SA"), _player("Dante Wilder", "SG", 250.0, "MIN"),
              _player("Omar Reyes", "PF", 204.0, "NYK")],
             [_player("Role Player", "F", 120.0, "POR"), _player("Another Guy", "UT", 95.25, "WAS"),
              _player("Instant Offense", "BE", 88.0, "CHA")]),
        _box(cereal, wind, 978.0, 987.5,
             [_player("Lionel Hart", "PG", 230.0, "PHX"), _player("Sami Kerr", "C", 219.0, "SAC")],
             [_player("Pax Delgado", "SG", 262.0, "ATL"), _player("Ezra Fontaine", "PF", 201.0, "MEM"),
              _player("Late Scratch", "C", 0.0, "HOU")]),
    ]
    summary = summarize_box_scores(boxes, "The Example League", 2027, 3, "H2H_POINTS", default_status="final")
    by_id = {t.team_id: t for t in (dunk, bricks, hoops, zone, cereal, wind)}
    for team in summary.teams:
        team.prev_record = by_id[team.team_id].prev
    summary.big_games = [
        BigGame(player="Victor Sol", fantasy_team="Hoop Dreams", started=True, slot="C", scoring_period=15,
                game_score=88.5, fantasy_points=88.5, pro_team="SA", opponent="LAL", date="2026-11-04",
                stats={"PTS": 41, "REB": 15, "AST": 6, "BLK": 5}, notes=["40-point game"]),
        BigGame(player="Andre Castillo", fantasy_team="Brick Layers", started=True, slot="PG", scoring_period=17,
                game_score=79.0, fantasy_points=79.0, pro_team="DAL", opponent="PHX", date="2026-11-06",
                stats={"PTS": 33, "REB": 11, "AST": 12}, notes=["triple-double"]),
        BigGame(player="Sleeper Pick", fantasy_team="Brick Layers", started=False, slot="BE", scoring_period=16,
                game_score=61.0, fantasy_points=61.0, pro_team="OKC", opponent="DEN", date="2026-11-05",
                stats={"PTS": 29, "REB": 9, "AST": 4, "STL": 3}),
    ]
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ai", action="store_true",
                   help="have a model write the story instead of the template (see the README's story writer section)")
    p.add_argument("--pdf", type=Path, metavar="FILE", help="also render the PDF at FILE")
    args = p.parse_args()
    load_dotenv_if_present()

    summary = sample_summary()
    story = write_story(summary, StoryConfig.from_env(), use_ai=args.ai, include_award=False)
    award = team_of_the_week(summary)
    plain = parse_story(story).plain()
    print(f"{award}\n\n{plain}" if award else plain)
    if args.pdf:
        from fantasy_recap.pdf import render_pdf

        print(f"wrote {render_pdf(summary, story, args.pdf)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
