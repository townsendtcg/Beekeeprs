# Serendipitous Beekeeprs weekly report

Power rankings, last week's recap and this week's moneylines, built from Sleeper's public API and served with GitHub Pages.

## Setup (one time)
1. Create a new public repo on GitHub and push these files to `main`.
2. Settings > Pages: Source "Deploy from a branch", branch `main`, folder `/docs`.
3. Settings > Actions > General > Workflow permissions: "Read and write permissions".
4. Your link is `https://<your-username>.github.io/<repo-name>/`.

## Every week
- The workflow runs Wednesdays at 10am ET, rebuilds `docs/index.html` and commits it. Old weeks are kept in `docs/weeks/`.
- Commentary lives in `notes/week-N.json` (N = the preview week). Without it the page still builds, with plain numbers-based blurbs.
- Pushing a notes file rebuilds the page automatically. You can also run it by hand from the Actions tab ("Run workflow").

## Run locally
`python generate.py` (or `WEEK=4 python generate.py`). No packages needed.

## Notes
- Regular season only. Playoff brackets aren't handled yet.
- Moneylines use Sleeper projections for the current starters, scored with league settings, a normal curve with a 34-point spread on the margin, and no vig.
