from types import SimpleNamespace

from fantasy_recap.config import StoryConfig
from fantasy_recap.espn import summarize_box_scores
from fantasy_recap.models import WeekSummary
from fantasy_recap.story import SYSTEM_PROMPT, build_prompt, template_story, write_story


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
    assert write_story(s, StoryConfig(api_key=None)) == template_story(s)


def test_write_story_empty_week():
    s = WeekSummary(league_name="Empty", season=2027, week=1, scoring="points")
    assert "No matchups" in write_story(s, StoryConfig(api_key=None))


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="  A fine recap.\n\nPart two.  ")])


def test_ai_story_sends_summary_to_claude(points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    client = SimpleNamespace(messages=FakeMessages())
    story = write_story(s, StoryConfig(api_key="sk-test", model="claude-sonnet-5-5"), client=client)
    assert story == "A fine recap.\n\nPart two."
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
