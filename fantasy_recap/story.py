"""Turn a WeekSummary into a 2-3 paragraph recap.

With an Anthropic API key the story is written by Claude from the structured
summary. Without one (or with --no-ai) a plain template story is used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import StoryConfig
from .models import Matchup, TeamWeek, WeekSummary

SYSTEM_PROMPT = """You write weekly recaps for a fantasy basketball league, in the voice of a trash-talking \
sports columnist writing for the league's group chat. The goal is to make the managers laugh.

Tone:
- Be genuinely funny, not just upbeat. Use roasts, absurd comparisons, mock-serious drama and dry asides. \
Every paragraph should have at least one real joke.
- Roast losing teams, ugly stat lines and bad lineup decisions. Hype the winners like they just won a title. \
Team names are fair game for jokes.
- Keep it good-natured: mock the teams and decisions, never the managers' personal lives.

Rules:
- Write three or four paragraphs of plain prose. No bullet points, no title, and no markdown except the \
headline lines below.
- Put a short, punchy headline (two to six words) on its own line above each paragraph, written as \
"## Headline".
- After the last paragraph, add one final line: "Pull quote: " followed by the single funniest sentence \
from your recap, copied word for word.
- Write every team name exactly as it appears in the data, including its spelling and capitalization.
- Use only the facts in the data you are given. Never invent players, stats, injuries, trades or quotes. \
Jokes and comparisons are fine as long as they don't pass off made-up facts as real.
- The recap is printed under a "Team of the Week" award line naming the highest scoring team \
("high_score_team" in highlights). Don't repeat that award or open with it; mention the team where its \
matchup comes up. No royalty or "champion of the universe" style hype for anyone.
- Cover every matchup at least briefly. Spend the most words on the closest game and the biggest blowout.
- Name the week's top player, and needle a manager who left big points on the bench if the data shows one.
- "big_games" lists the best single-game box scores of the week. Work in the best one or two, with their \
stat lines, and call out any that were wasted on a fantasy bench (started is false).
- If a matchup's status is "in_progress", describe who is leading rather than who won.
- In a category league, talk about category records (for example 6-3) and which categories swung it, not points.
- Keep it under about 400 words."""


@dataclass
class StoryParts:
    """A recap split into (headline, paragraph) sections plus an optional pull quote."""

    sections: list[tuple[str, str]] = field(default_factory=list)  # headline may be ""
    pull_quote: str = ""

    def plain(self) -> str:
        """The recap as plain text for the terminal and group chat (headlines kept, quote dropped)."""
        return "\n\n".join(f"{h}\n{p}" if h else p for h, p in self.sections)


_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019 "


def parse_story(text: str) -> StoryParts:
    """Split a recap written with '## Headline' lines and a trailing 'Pull quote:' line."""
    parts = StoryParts()
    headline, para = "", []

    def flush():
        nonlocal headline, para
        if para:
            parts.sections.append((headline, " ".join(para)))
            headline, para = "", []

    for raw in text.splitlines():
        line = raw.strip()
        quote = re.match(r"(?i)^\**pull quote\**\s*:\s*(.+)$", line)
        if quote:
            flush()
            parts.pull_quote = quote.group(1).strip(_QUOTE_CHARS + "*")
        elif line.startswith("#"):
            flush()
            headline = line.lstrip("#").strip().strip("*")
        elif not line:
            flush()
        else:
            para.append(line)
    flush()
    return parts


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
    include_award: bool = True,
) -> str:
    """Return the recap text. Falls back to the template when AI isn't available."""
    if not summary.matchups:
        return f"No matchups were found for week {summary.week} of {summary.league_name}."

    config = config or StoryConfig.from_env()
    if use_ai and (client is not None or config.api_key):
        story = ai_story(summary, config, client=client)
    else:
        story = template_story(summary)
    award = team_of_the_week(summary) if include_award else ""
    return f"{award}\n\n{story}" if award else story


def team_of_the_week(summary: WeekSummary) -> str:
    """Award line for the top-scoring team, or "" when there's no meaningful score yet."""
    team = summary.high_score_team
    if summary.scoring != "points" or team is None or team.score <= 0:
        return ""
    score = f"{team.score:,.0f}" if float(team.score).is_integer() else f"{team.score:,.1f}"
    so_far = "" if all(m.status == "final" for m in summary.real_matchups) else " (so far)"
    return f"Team of the Week{so_far}: {team.name} ({score})"


def ai_story(summary: WeekSummary, config: StoryConfig, client: Any = None) -> str:
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=config.api_key)
    response = client.messages.create(
        model=config.model,
        # Thinking tokens count against max_tokens, so this must leave room beyond the ~400-word story.
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(summary)}],
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The model hit max_tokens before finishing the story.")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
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


def _poss(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


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
        paragraphs.append(("## Down to the wire\n" if final else "## Still anybody's game\n") + " ".join(first))

    # Paragraph 2: the rest of the slate.
    rest = [m for m in games if m is not close and m is not blowout]
    if rest:
        lines = []
        for m in rest:
            line = _score_line(m)
            if m.leader and _star(m.leader, scoring):
                line += f", behind {_star(m.leader, scoring)}"
            lines.append(line)
        paragraphs.append("## Around the league\nElsewhere, " + "; ".join(lines) + ".")
    byes = [m.home.name for m in summary.matchups if m.is_bye]
    if byes:
        bye_line = f"{', '.join(byes)} had the week off."
        if rest:
            paragraphs[-1] += " " + bye_line
        else:
            paragraphs.append("## Day off\n" + bye_line)

    # Paragraph 3: individual notes.
    notes: list[str] = []
    best = summary.big_games[0] if summary.big_games else None
    if best:
        extra = f" ({_join(best.notes)})" if best.notes else ""
        vs = f" against {best.opponent}" if best.opponent else ""
        where = f"for {best.fantasy_team}" if best.started else f"on {_poss(best.fantasy_team)} bench, sadly"
        notes.append(f"The game of the week belonged to {best.player}, who went for {best.line()}{extra}{vs} {where}.")
        wasted = next((g for g in summary.big_games[1:5] if not g.started), None)
        if wasted and best.started:
            notes.append(f"{_poss(wasted.player)} {wasted.line()} night was wasted on {_poss(wasted.fantasy_team)} bench.")
    elif scoring == "points" and summary.top_player and summary.top_player[0].points > 0:
        p, t = summary.top_player
        notes.append(f"The top individual line belonged to {p.name}, who put up {_fmt(p.points)} for {t.name}.")
    if best and scoring == "points" and summary.top_player and summary.top_player[0].points > 0:
        p, t = summary.top_player
        notes.append(f"Over the whole week, {p.name} led everyone with {_fmt(p.points)} fantasy points for {t.name}.")
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
        paragraphs.append("## Stars and scrubs\n" + " ".join(notes))

    return "\n\n".join(paragraphs[:4])
