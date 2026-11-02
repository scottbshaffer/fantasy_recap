# Weekly summaries

`scripts/weekly-export.sh` writes one file per finished week here, named `<season>-week-<NN>.json`
(for example `2027-week-03.json`; the 2026-27 season is `2027`).

Each file holds the league name, season, week, scoring type, `status` (`final` or `in_progress`),
every matchup (teams, records, scores, starters with fantasy points, bench points, category results),
`highlights` (closest game, biggest blowout, high score, top player, most points left on the bench)
and `big_games`: the week's best single-game box scores by rostered players, with the fantasy team,
whether the player was started or benched, the stat line and any notes like "triple-double".
