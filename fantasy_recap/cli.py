"""Command line entry point: `python -m fantasy_recap --week 3`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .config import SPORTS, STORY_PROVIDERS, EspnConfig, StoryConfig, load_dotenv_if_present
from .espn import EspnError, export_summary, fetch_week
from .providers import LABELS
from .story import parse_story, story_provider, team_of_the_week, write_story


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fantasy_recap",
        description="Write a short story about this week's ESPN fantasy basketball or football matchups.",
    )
    week = p.add_mutually_exclusive_group()
    week.add_argument("--week", type=int, help="ESPN matchup period (defaults to the current week)")
    week.add_argument("--previous", action="store_true",
                      help="use the week before the current one (the week that just ended, when run on a Monday)")
    p.add_argument("--sport", choices=SPORTS, help="league sport (default: ESPN_SPORT or basketball)")
    p.add_argument("--league-id", type=int, help="overrides ESPN_LEAGUE_ID")
    p.add_argument("--year", type=int,
                   help="season; basketball is named by the year it ends (2026-27 is 2027), football by the year "
                        "it starts; overrides ESPN_YEAR")
    p.add_argument("--provider", choices=("auto",) + STORY_PROVIDERS,
                   help="who writes the story (default: FANTASY_RECAP_PROVIDER, or the first one available: "
                        "FANTASY_RECAP_BASE_URL, ANTHROPIC_API_KEY, OPENAI_API_KEY, the claude CLI, the codex CLI, "
                        "then the template)")
    p.add_argument("--model", help="model id for the story writer (default: FANTASY_RECAP_MODEL, else "
                                   "claude-sonnet-5-5 for Claude, gpt-6.1-terra for OpenAI, Codex's own default)")
    p.add_argument("--no-ai", action="store_true", help="skip the model and use the built-in template story")
    p.add_argument("--json", action="store_true", help="print the matchup summary as JSON instead of a story")
    p.add_argument("--export", nargs="?", const=Path("summaries"), type=Path, metavar="DIR",
                   help="write the summary JSON to DIR/<year>-week-<NN>.json (default DIR: summaries) "
                        "and print the path, instead of a story")
    p.add_argument("--big-games", type=int, default=10, metavar="N",
                   help="how many top single-game player performances to include (0 to skip; default 10)")
    p.add_argument("--out", type=Path, help="also write the output to this file")
    p.add_argument("--pdf", type=Path, metavar="FILE",
                   help="also render the recap as a one-page, phone-sized PDF at FILE")
    p.add_argument("--team-colors", type=Path, metavar="FILE",
                   help='JSON file of PDF team colors, keyed by team name or ESPN team id, e.g. {"The Haliban": "#e63946"}')
    p.add_argument("--no-logos", action="store_true", help="use colored initials instead of ESPN team logos in the PDF")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.pdf and (args.json or args.export):
        parser.error("--pdf renders the story, so it can't be combined with --json or --export")
    logging.basicConfig(level=logging.WARNING, format="note: %(message)s")
    load_dotenv_if_present()

    try:
        espn_config = EspnConfig.from_env(league_id=args.league_id, year=args.year, sport=args.sport)
        summary = fetch_week(espn_config, week=args.week, previous=args.previous, big_games=args.big_games)
    except (ValueError, EspnError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.export:
        print(export_summary(summary, args.export))
        return 0

    story = None
    if args.json:
        output = json.dumps(summary.to_dict(), indent=2)
    else:
        try:
            story_config = StoryConfig.from_env(model=args.model, provider=args.provider)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        provider = story_provider(story_config, use_ai=not args.no_ai)
        if not args.no_ai and provider == "template" and not story_config.provider:
            print("note: no story writer found, using the template story. Set ANTHROPIC_API_KEY or "
                  "OPENAI_API_KEY, install Claude Code or Codex, or see FANTASY_RECAP_PROVIDER in "
                  ".env.example.", file=sys.stderr)
        try:
            story = write_story(summary, story_config, use_ai=provider != "template", include_award=False)
        except Exception as exc:  # API and CLI errors shouldn't leave the user with nothing
            print(f"note: {LABELS.get(provider, provider)} couldn't write the story ({exc}); using the template.",
                  file=sys.stderr)
            story = write_story(summary, story_config, use_ai=False, include_award=False)
        award = team_of_the_week(summary)
        plain = parse_story(story).plain()
        output = f"{award}\n\n{plain}" if award else plain

    print(output)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    if args.pdf:
        # Imported lazily so the plain-text and export paths work without reportlab installed.
        from .pdf import fetch_logos, load_team_colors, render_pdf

        try:
            colors = load_team_colors(args.team_colors) if args.team_colors else None
        except (OSError, ValueError) as exc:
            print(f"error: couldn't read --team-colors: {exc}", file=sys.stderr)
            return 2
        logos = None if args.no_logos else fetch_logos(summary, espn_config)
        path = render_pdf(summary, story, args.pdf, team_colors=colors, logos=logos)
        print(f"wrote {path}", file=sys.stderr)
    return 0
