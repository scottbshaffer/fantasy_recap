from fantasy_recap.espn import apply_records, records_through, summarize_box_scores


def game(period, home, away, hp, ap, winner):
    return {"matchupPeriodId": period, "winner": winner,
            "home": {"teamId": home, "totalPoints": hp}, "away": {"teamId": away, "totalPoints": ap}}


SCHEDULE = [
    game(1, 1, 2, 900, 950, "AWAY"),
    game(1, 3, 4, 800, 700, "HOME"),
    game(2, 1, 2, 1012.5, 1004.0, "HOME"),
    game(2, 3, 4, 1201.0, 803.25, "HOME"),
    game(3, 1, 3, 0, 0, "UNDECIDED"),
    {"matchupPeriodId": 2, "winner": "UNDECIDED", "home": {"teamId": 5, "totalPoints": 900}},  # bye
]


def test_records_through_counts_only_decided_games_up_to_the_week():
    assert records_through(SCHEDULE, 1) == {1: (0, 1, 0, 900.0), 2: (1, 0, 0, 950.0),
                                            3: (1, 0, 0, 800.0), 4: (0, 1, 0, 700.0)}
    assert records_through(SCHEDULE, 3)[1] == (1, 1, 0, 1912.5)


def test_standings_movement_and_upset(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 2, "H2H_POINTS")
    apply_records(s, SCHEDULE)
    dunk = next(t for t in s.teams if t.name == "Dunk Dynasty")
    assert dunk.record == "1-1-0" and dunk.prev_record == (0, 1, 0, 900.0)
    # Dunk Dynasty (0-1) beat Brick Layers (1-0): an upset
    first = s.real_matchups[0]
    assert first.is_upset and not s.real_matchups[1].is_upset
    table = {r.team.name: r for r in s.standings}
    assert table["Hoop Dreams"].rank == 1
    assert table["Dunk Dynasty"].movement == table["Dunk Dynasty"].prev_rank - table["Dunk Dynasty"].rank
    assert s.to_dict()["highlights"]["upset_winners"] == ["Dunk Dynasty"]


def test_bench_cost_it(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    # Brick Layers lost by 8.5 with 155 on the bench
    assert s.real_matchups[0].bench_cost_it
    assert not s.real_matchups[1].bench_cost_it
