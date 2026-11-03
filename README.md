# fantasy_recap

Reads a week of matchups from your ESPN fantasy basketball league (lineups, scores, bench points, close games, blowouts) and writes a two to three paragraph story about it.

The story is written by Claude when an Anthropic API key is set. Without one, a built-in template writes a plainer recap, so the tool always works.

## Setup

Python 3.10 or newer.

```bash
git clone https://github.com/scottbshaffer/fantasy_recap.git
cd fantasy_recap
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`:

| Variable | Needed | What it is |
| --- | --- | --- |
| `ESPN_LEAGUE_ID` | yes | The number after `leagueId=` in your league's URL on fantasy.espn.com |
| `ESPN_YEAR` | no | Season, named by the year it ends (2026-27 is `2027`). Picked automatically if blank. |
| `ESPN_S2` | private leagues | The `espn_s2` cookie from your browser |
| `ESPN_SWID` | private leagues | The `SWID` cookie from your browser, braces included or not |
| `ANTHROPIC_API_KEY` | no | Lets Claude write the story. Get one at console.anthropic.com. |
| `FANTASY_RECAP_MODEL` | no | Claude model id. Defaults to `claude-sonnet-5-5`. |

`.env` is in `.gitignore`. Keep it that way: the ESPN cookies work like a password for your ESPN account.

### Finding `espn_s2` and `SWID`

ESPN doesn't use an API key. For a private league, the tool signs in with the same two cookies your browser uses.

1. Sign in at fantasy.espn.com in Chrome, Edge or Firefox and open your league.
2. Open the developer tools (F12, or right click and Inspect).
3. Chrome or Edge: Application tab, then Cookies, then `https://fantasy.espn.com`. Firefox: Storage tab, then Cookies.
4. Copy the value of `espn_s2` (a long string) into `ESPN_S2`, and `SWID` (looks like `{XXXXXXXX-XXXX-...}`) into `ESPN_SWID`.

The cookies last until you sign out of ESPN in that browser. If the tool starts failing with an access error, grab fresh ones.

## Usage

```bash
# Current week
python -m fantasy_recap

# A specific matchup week, saved to a file too
python -m fantasy_recap --week 3 --out recaps/week3.txt

# Also make a one-page, phone-sized PDF (Team of the Week, stat tiles, scoreboard, recap,
# Game of the Week and standings) to share in a group chat
python -m fantasy_recap --previous --pdf recaps/week3.pdf

# Pick your own team colors (keys are team names or ESPN team ids), or skip ESPN logos
python -m fantasy_recap --previous --pdf recaps/week3.pdf --team-colors colors.json --no-logos

# Skip Claude and use the template story
python -m fantasy_recap --no-ai

# See the structured data the story is written from
python -m fantasy_recap --week 3 --json

# Last week's matchups (handy on a Monday, once ESPN has rolled over)
python -m fantasy_recap --previous

# Write the summary to summaries/<season>-week-<NN>.json instead of a story
python -m fantasy_recap --previous --export
```

Other flags: `--league-id` and `--year` override the env vars, `--model` picks a different Claude model, and `--big-games N` sets how many standout single games to include (default 10, `0` to skip). If you `pip install -e .`, the same thing runs as `fantasy-recap`.

"Week" means ESPN's matchup period, the number shown on your league's scoreboard. Running it mid-week gives a "so far" story about who's leading.

## What goes into the story

For each matchup the tool pulls:

- both teams, their season record and score (or category record in category leagues)
- every starter's fantasy points for the week, and the top performer
- points left on the bench and the best bench player (points leagues)
- categories won and lost (category leagues)
- whether the matchup is final or still going

It also goes through each day of the week and pulls every rostered player's single-game box score, keeping the best games (by fantasy points in points leagues, or a standard game score in category leagues). Each one notes the fantasy team, whether the player was started or sat on the bench, the stat line and milestones like a triple-double or a 40-point game.

It then picks out league highlights: the closest game, the biggest blowout, the high score, the top player and the most points left on the bench. That summary goes to Claude with instructions to write two or three paragraphs and use only those facts. Head to head points and category leagues are both supported.

## Weekly story without an API key

If you'd rather not pay for an Anthropic key, your server can export each week's summary to this repo, and Claude (in the Claude app) can write the story from it on a schedule.

1. Clone the repo on your server, set up the virtualenv and `.env` as above, and make sure `git push` works from there (an SSH deploy key with write access is the simplest).
2. Test it once: `./scripts/weekly-export.sh`. It pulls, runs `python -m fantasy_recap --previous --export summaries`, and commits and pushes the new file.
3. Schedule it for Monday at 9am Eastern, after the fantasy week ends Sunday night. With systemd, edit the user and paths in `deploy/systemd/` and run:

   ```bash
   sudo cp deploy/systemd/fantasy-recap-export.* /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now fantasy-recap-export.timer
   ```

   Or use cron with `deploy/crontab.example`.

Each week lands at `summaries/<season>-week-<NN>.json`, for example `summaries/2027-week-03.json`. `summaries/README.md` describes what's in it. To redo a specific week, run `EXPORT_ARGS="--week 3" ./scripts/weekly-export.sh`.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use fake ESPN data and a fake Claude client, so they need no network or credentials.

Layout:

- `fantasy_recap/espn.py` reads the league with the [espn-api](https://github.com/cwendt94/espn-api) package and turns box scores into a `WeekSummary`
- `fantasy_recap/games.py` finds the week's biggest single-game performances
- `fantasy_recap/models.py` holds the summary data classes and the highlight logic
- `fantasy_recap/story.py` has the Claude prompt and the template fallback
- `fantasy_recap/cli.py` is the command line tool
