"""Turn a WeekSummary into a 2-3 paragraph recap.

With an Anthropic API key the story is written by Claude from the structured
summary. Without one (or with --no-ai) a plain template story is used.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .config import StoryConfig
from .models import Matchup, TeamWeek, WeekSummary

SYSTEM_PROMPT = """You write weekly recaps for a fantasy basketball league, in the voice of a sharp, \
playful sports columnist writing for the league's group chat.

Rules:
- Write exactly two or three paragraphs of plain prose. No headings, no bullet points, no markdown, no title.
- Use only the facts in the data you are given. Never invent players, stats, injuries, trades or quotes.
- Cover every matchup at least briefly. Spend the most words on the closest game and the biggest blowout.
- Name the week's top player, and needle a manager who left big points on the bench if the data shows one.
- If a matchup's status is "in_progress", describe who is leading rather than who won.
- In a category league, talk about category records (for example 6-3) and which categories swung it, not points.
- Keep it under about 300 words."""


def build_prompt(summary: WeekSummary) -> str:
    data = json.dumps(summary.to_dict(), indent=2)
    return (
        f"Here is week {summary.week} of {summary.league_name} ({summary.season} season) "
        f"as JSON. Write the recap.\n\n{data}"
    )


def write_story(
    summary: WeekSummary,
    config: Optional[StoryConfig] = None,
    use_ai: bool = True,
    client: Any = None,
) -> str:
    """Return the recap text. Falls back to the template when AI isn't available."""
    if not summary.matchups:
        return f"No matchups were found for week {summary.week} of {summary.league_name}."

    config = config or StoryConfig.from_env()
    if use_ai and (client is not None or config.api_key):
        return ai_story(summary, config, client=client)
    return template_story(summary)


def ai_story(summary: WeekSummary, config: StoryConfig, client: Any = None) -> str:
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=config.api_key)
    response = client.messages.create(
        model=config.model,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(summary)}],
    )
    text = "".join(getattr(block, "text", "") for block in response.content).strip()
    if not text:
        raise RuntimeError("The model returned an empty story.")
    return text


# ---------------------------------------------------------------------------
# Template fallback


def _fmt(x: float) -> str:
    return f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}"


def _score_line(m: Matchup) -> str:
    leader, trailer = m.leader, m.trailer
    if m.scoring == "categories":
        if leader is None:
            return f"{m.home.name} and {m.away.name} are dead even at {m.home.category_record}"
        verb = "beat" if m.status == "final" else "leads"
        return f"{leader.name} {verb} {trailer.name} {leader.category_record} in categories"
    if leader is None:
        return f"{m.home.name} and {m.away.name} are tied at {_fmt(m.home.score)}"
    verb = "beat" if m.status == "final" else "leads"
    return f"{leader.name} {verb} {trailer.name} {_fmt(leader.score)} to {_fmt(trailer.score)}"


def _join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _star(team: TeamWeek, scoring: str) -> str:
    if scoring == "categories":  # ESPN doesn't give player points in category leagues
        return ""
    top = team.top_performer
    return f"{top.name} ({_fmt(top.points)})" if top and top.points > 0 else ""


def template_story(summary: WeekSummary) -> str:
    games = summary.real_matchups
    final = all(m.status == "final" for m in games)
    scoring = summary.scoring
    paragraphs: list[str] = []

    # Paragraph 1: the headline games.
    first: list[str] = []
    close = summary.closest
    blowout = summary.biggest_blowout
    if close:
        unit = "categories" if scoring == "categories" else "points"
        if close.margin == 1:
            unit = unit[:-3] + "y" if unit == "categories" else "point"
        if final:
            intro = f"Week {summary.week} in {summary.league_name} came down to the wire in one game"
        else:
            intro = f"Week {summary.week} in {summary.league_name} is still being played, and the tightest race so far"
        first.append(f"{intro}: {_score_line(close)}, a margin of just {_fmt(close.margin)} {unit}.")
        if scoring == "categories" and close.leader and close.leader.categories_won:
            verb = "took" if close.status == "final" else "has taken"
            first.append(f"{close.leader.name} {verb} {_join(close.leader.categories_won)}.")
        if close.leader and _star(close.leader, scoring):
            first.append(f"{_star(close.leader, scoring)} did the heavy lifting for {close.leader.name}.")
    if blowout and blowout is not close:
        tail = "and it was never really close" if blowout.status == "final" else "and it hasn't been close"
        first.append(f"On the other end of the spectrum, {_score_line(blowout)}, {tail}.")
    if first:
        paragraphs.append(" ".join(first))

    # Paragraph 2: the rest of the slate.
    rest = [m for m in games if m is not close and m is not blowout]
    if rest:
        lines = []
        for m in rest:
            line = _score_line(m)
            if m.leader and _star(m.leader, scoring):
                line += f", behind {_star(m.leader, scoring)}"
            lines.append(line)
        paragraphs.append("Around the league, " + "; ".join(lines) + ".")
    byes = [m.home.name for m in summary.matchups if m.is_bye]
    if byes:
        bye_line = f"{', '.join(byes)} had the week off."
        if rest:
            paragraphs[-1] += " " + bye_line
        else:
            paragraphs.append(bye_line)

    # Paragraph 3: individual notes.
    notes: list[str] = []
    if scoring == "points" and summary.top_player and summary.top_player[0].points > 0:
        p, t = summary.top_player
        notes.append(f"The top individual line belonged to {p.name}, who put up {_fmt(p.points)} for {t.name}.")
    if summary.scoring == "points" and summary.high_score_team:
        t = summary.high_score_team
        notes.append(f"{t.name} posted the week's high score with {_fmt(t.score)}.")
    if summary.scoring == "points" and summary.most_bench_points:
        t = summary.most_bench_points
        bench_top = t.top_bench_player
        extra = ""
        if bench_top and bench_top.points == t.bench_points:
            extra = f", all of it from {bench_top.name}"
        elif bench_top:
            extra = f", including {_fmt(bench_top.points)} from {bench_top.name}"
        notes.append(f"Meanwhile {t.name} left {_fmt(t.bench_points)} points on the bench{extra}. Ouch.")
    if notes:
        paragraphs.append(" ".join(notes))

    return "\n\n".join(paragraphs[:3])
