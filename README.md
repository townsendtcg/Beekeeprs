# Serendipitous Beekeeprs weekly report

Live at https://townsendtcg.github.io/Beekeeprs/

Storylines, power rankings, a recap with an actual vs best-lineup toggle, weekly awards, the Hall of Bench Pain, a luck meter and moneylines for the coming week. Built from Sleeper's public API and served with GitHub Pages from `/docs`.

## Schedule
The workflow rebuilds the page three times a week (Eastern time):
- Tuesday 8am: recap of the week that just finished, and the new preview
- Thursday 2pm: odds refreshed with updated lineups before the first game
- Sunday 10am: final odds with Sunday lineups

Every build also saves `docs/weeks/week-N.html`, so past weeks stay reachable from the week picker. Missing past weeks are backfilled automatically.

## Commentary
Commish writing lives in `notes/week-N.json`, where N is the preview week. Everything else (storylines, awards, bench pain, luck) is computed from the data. Without a notes file the page still builds, with short data-driven blurbs. Pushing a notes file rebuilds the page. You can also rebuild by hand from the Actions tab ("Run workflow").

## Run locally
`python generate.py` builds the latest week. `WEEK=2 python generate.py` rebuilds one archive week. No packages needed.

## Notes
- Regular season only. Playoff brackets aren't handled yet.
- Moneylines use Sleeper projections for the current starters, scored with league settings, a normal curve with a 34-point spread on the margin, and no vig. Archive pages show lines rebuilt after the fact next to the final scores.
- "Best lineup" is the highest-scoring legal lineup from each full roster that week.
