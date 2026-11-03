from types import SimpleNamespace

import pytest

from fantasy_recap.config import StoryConfig
from fantasy_recap.espn import summarize_box_scores
from fantasy_recap.models import WeekSummary
from fantasy_recap.story import SYSTEM_PROMPT, build_prompt, team_of_the_week, template_story, write_story


def test_template_story_points(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    story = template_story(s)
    paragraphs = story.split("\n\n")
    assert 2 <= len(paragraphs) <= 3
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
