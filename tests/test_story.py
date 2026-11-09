from types import SimpleNamespace

import pytest

from fantasy_recap.config import StoryConfig
from fantasy_recap.espn import summarize_box_scores
from fantasy_recap.models import WeekSummary
from fantasy_recap.story import SYSTEM_PROMPT, build_prompt, team_of_the_week, template_story, write_story


def test_template_story_points(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    story = template_story(s)
    from fantasy_recap.story import parse_story

    assert 2 <= len(parse_story(story).sections) <= 3
    assert "Dunk Dynasty beat Brick Layers 1012.5 to 1004" in story
    assert "Victor Wembanyama" in story
    assert "Pick and Roll had the week off" in story
    assert "Brick Layers left 155 points on the bench, all of it from Sleeper Pick" in story


def test_template_story_categories(category_box_scores):
    s = summarize_box_scores(category_box_scores, "Cats", 2027, 2, "H2H_CATEGORY")
    story = template_story(s)
    assert "Week 2 in Cats is still being played" in story
    assert "Stat Stuffers leads Punt FT% 5-4-0" in story
    assert "Sabonis" not in story
    assert "Stat Stuffers has taken AST, BLK, PTS, REB and STL." in story
    assert "bench" not in story


def test_write_story_without_key_uses_template(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    assert write_story(s, StoryConfig(api_key=None)) == f"{team_of_the_week(s)}\n\n{template_story(s)}"


def test_team_of_the_week_line(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    assert team_of_the_week(s) == "Team of the Week: Hoop Dreams (1,201)"


def test_team_of_the_week_skipped_for_categories(category_box_scores):
    s = summarize_box_scores(category_box_scores, "Cats", 2027, 2, "H2H_CATEGORY")
    assert team_of_the_week(s) == ""
    assert not write_story(s, StoryConfig(api_key=None)).startswith("Team of the Week")


def test_write_story_empty_week():
    s = WeekSummary(league_name="Empty", season=2027, week=1, scoring="points")
    assert "No matchups" in write_story(s, StoryConfig(api_key=None))


class FakeMessages:
    def __init__(self, stop_reason="end_turn"):
        self.kwargs = None
        self.stop_reason = stop_reason

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text="  A fine recap.\n\nPart two.  "),
            ],
        )


def test_ai_story_rejects_truncated_output(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    client = SimpleNamespace(messages=FakeMessages(stop_reason="max_tokens"))
    with pytest.raises(RuntimeError, match="max_tokens"):
        write_story(s, StoryConfig(api_key="sk-test"), client=client)


def test_ai_story_sends_summary_to_claude(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    client = SimpleNamespace(messages=FakeMessages())
    story = write_story(s, StoryConfig(api_key="sk-test", model="claude-sonnet-5-5"), client=client)
    assert story == f"{team_of_the_week(s)}\n\nA fine recap.\n\nPart two."
    kwargs = client.messages.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["system"] == SYSTEM_PROMPT
    assert "Victor Wembanyama" in kwargs["messages"][0]["content"]


def test_no_ai_flag_skips_client(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    client = SimpleNamespace(messages=FakeMessages())
    write_story(s, StoryConfig(api_key="sk-test"), use_ai=False, client=client)
    assert client.messages.kwargs is None


def test_prompt_mentions_week(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    assert build_prompt(s).startswith("Here is week 3 of Test League")


def test_parse_story_headlines_and_pull_quote():
    from fantasy_recap.story import parse_story

    text = "## Big Night\nFirst line\ncontinues.\n\n## Ouch\nSecond.\n\nPull quote: “Second.”"
    parts = parse_story(text)
    assert parts.sections == [("Big Night", "First line continues."), ("Ouch", "Second.")]
    assert parts.pull_quote == "Second."
    assert parts.plain() == "Big Night\nFirst line continues.\n\nOuch\nSecond."


def test_parse_story_plain_paragraphs():
    from fantasy_recap.story import parse_story

    parts = parse_story("One.\n\nTwo.")
    assert parts.sections == [("", "One."), ("", "Two.")] and parts.pull_quote == ""


def test_template_story_has_headlines(points_box_scores):
    from fantasy_recap.story import parse_story

    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    headlines = [h for h, _ in parse_story(template_story(s)).sections]
    assert len(headlines) >= 2 and all(headlines)


def test_parse_story_ignores_self_corrections():
    from fantasy_recap.story import parse_story

    text = ("## One\nKobe Sanders gave The Haliban -3.0 points. That’s a withdrawal.\n\n"
            "Pull quote: That is a withdrawal.\n"
            "Wait — that sentence isn't accurate to my recap; the correct line is below.\n"
            "Pull quote: That's a withdrawal.")
    parts = parse_story(text)
    assert parts.sections == [("One", "Kobe Sanders gave The Haliban -3.0 points. That’s a withdrawal.")]
    assert parts.pull_quote == "That's a withdrawal."
    assert "Wait" not in parts.plain()


def test_parse_story_drops_made_up_pull_quote():
    from fantasy_recap.story import parse_story

    assert parse_story("Real sentence.\n\nPull quote: A sentence that isn't there.").pull_quote == ""


def test_prompt_forbids_notes():
    assert "Never add notes, corrections or comments about your own writing" in SYSTEM_PROMPT


def test_standings_take_parsed_and_kept_out_of_paragraphs():
    from fantasy_recap.story import parse_story

    text = ("## One\nA recap line.\n\nStandings take: Dunk Dynasty is 3-0 and smug about it.\n"
            "Pull quote: A recap line.")
    parts = parse_story(text)
    assert parts.sections == [("One", "A recap line.")]
    assert parts.standings_take == "Dunk Dynasty is 3-0 and smug about it."
    assert parts.pull_quote == "A recap line."
    assert parts.plain().endswith("\n\nStandings take: Dunk Dynasty is 3-0 and smug about it.")


def test_template_standings_take(points_box_scores):
    from fantasy_recap.espn import apply_records
    from fantasy_recap.story import parse_story, standings_take

    schedule = [
        {"matchupPeriodId": 1, "winner": "AWAY", "home": {"teamId": 1, "totalPoints": 900},
         "away": {"teamId": 2, "totalPoints": 950}},
        {"matchupPeriodId": 1, "winner": "HOME", "home": {"teamId": 3, "totalPoints": 800},
         "away": {"teamId": 4, "totalPoints": 700}},
        {"matchupPeriodId": 2, "winner": "HOME", "home": {"teamId": 1, "totalPoints": 1012.5},
         "away": {"teamId": 2, "totalPoints": 1004}},
        {"matchupPeriodId": 2, "winner": "HOME", "home": {"teamId": 3, "totalPoints": 1201},
         "away": {"teamId": 4, "totalPoints": 803.25}},
    ]
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 2, "H2H_POINTS")
    apply_records(s, schedule)
    take = standings_take(s)
    assert take.startswith("Hoop Dreams sits alone on top at 2-0.")
    assert "made the biggest jump" in take and "took the biggest tumble" in take
    assert parse_story(template_story(s)).standings_take == take


def test_prompt_asks_for_standings_take():
    assert '"Standings take: "' in SYSTEM_PROMPT
