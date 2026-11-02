"""Command line entry point: `python -m fantasy_recap --week 3`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .config import EspnConfig, StoryConfig, load_dotenv_if_present
from .espn import EspnError, fetch_week
from .story import write_story


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fantasy_recap",
        description="Write a short story about this week's ESPN fantasy basketball matchups.",
    )
    p.add_argument("--week", type=int, help="ESPN matchup period (defaults to the current week)")
    p.add_argument("--league-id", type=int, help="overrides ESPN_LEAGUE_ID")
    p.add_argument("--year", type=int, help="season, named by the year it ends (2026-27 is 2027); overrides ESPN_YEAR")
    p.add_argument("--model", help="Claude model id (default: FANTASY_RECAP_MODEL or claude-sonnet-5-5)")
    p.add_argument("--no-ai", action="store_true", help="skip Claude and use the built-in template story")
    p.add_argument("--json", action="store_true", help="print the matchup summary as JSON instead of a story")
    p.add_argument("--out", type=Path, help="also write the output to this file")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv_if_present()

    try:
        espn_config = EspnConfig.from_env(league_id=args.league_id, year=args.year)
        summary = fetch_week(espn_config, week=args.week)
    except (ValueError, EspnError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        output = json.dumps(summary.to_dict(), indent=2)
    else:
        story_config = StoryConfig.from_env(model=args.model)
        if not args.no_ai and not story_config.api_key:
            print("note: ANTHROPIC_API_KEY isn't set, using the template story.", file=sys.stderr)
        try:
            output = write_story(summary, story_config, use_ai=not args.no_ai)
        except Exception as exc:  # API errors shouldn't leave the user with nothing
            print(f"note: Claude couldn't write the story ({exc}); using the template.", file=sys.stderr)
            output = write_story(summary, story_config, use_ai=False)

    print(output)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    return 0
