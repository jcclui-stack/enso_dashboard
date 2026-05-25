# ENSO Dashboard — auto-rebuild

A self-contained ENSO monitoring page (Niño 3.4 SST, ONI/RONI, SOI, MEI.v2) with
current values, 36-month history charts, phase thresholds, and forecast
probabilities. The numeric indices are **fetched live** from official sources and
the page is **regenerated** on a schedule. No database, no backend server — the
output is a single static `enso-dashboard.html`.

## Files

| File | Purpose |
|------|---------|
| `build_dashboard.py` | The generator. Fetches data, computes phases/trends/history, writes the HTML. |
| `forecast_config.json` | **Hand-edited** forecast probabilities + status text (see below). |
| `_style_block.html` | The page's CSS, kept separate so the script can inject it verbatim. |
| `data_cache.json` | Last successfully-fetched data. Auto-written; acts as a fallback if a source is unreachable. |
| `.github/workflows/update.yml` | GitHub Action that reruns the script on a schedule and deploys to Pages. |
| `.nojekyll` | Tells GitHub Pages to serve files as-is (no Jekyll processing). |

## Quick start (local)

```bash
python build_dashboard.py            # fetch live data + rebuild
python build_dashboard.py --offline  # rebuild from cache only (no network)
open enso-dashboard.html
```

Only the Python standard library is required — no `pip install`.

## What's automated vs. manual

**Automated (fetched every run):** the four index values, their trends, phase
classification, and the 36-month history charts. These come from clean machine-
readable data files:

| Index | Source file |
|-------|-------------|
| ONI | `cpc.ncep.noaa.gov/data/indices/oni.ascii.txt` |
| Niño 3.4 | `cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii` |
| SOI | `bom.gov.au/climate/enso/soi_monthly.txt` |
| MEI.v2 | `psl.noaa.gov/enso/mei/data/meiv2.data` |

> ⚠️ **Verify these URLs once in your own environment.** Agencies occasionally
> move or rename files, and the exact SOI filename in particular can vary. If a
> fetch fails, the script logs a `[warn]` and falls back to the cached value, so
> the page never breaks — but the affected index won't refresh until the URL is
> fixed in `SOURCES` at the top of `build_dashboard.py`.

**Manual (edit `forecast_config.json`):** the El Niño / strong / super
probabilities, the status banner text, and the season-by-season probability bars.
These are **not** published as a machine-readable feed — they come from the prose
of the CPC ENSO Diagnostic Discussion. Update them when CPC issues a new one (the
**second Thursday of each month**). It's a 2-minute edit; the values change slowly.

## Deploying to GitHub Pages

The workflow (`.github/workflows/update.yml`) does everything automatically once
the repo is set up. **One-time setup:**

1. Push this project to a GitHub repo (default branch `main`).
2. In the repo, go to **Settings → Pages**, and under **Build and deployment →
   Source**, choose **GitHub Actions** (not "Deploy from a branch").
3. That's it. The workflow will build and deploy on its own.

The workflow runs on three triggers: a schedule (Tuesdays and Fridays at 06:00
UTC), any push to `main`, and the manual **Run workflow** button on the **Actions**
tab. To deploy immediately the first time, push to `main` or click Run workflow.

Your site will be live at `https://<username>.github.io/<repo>/`. The deploy job
prints the exact URL in its summary, and Pages serves `index.html` (the workflow
publishes a copy of `enso-dashboard.html` under that name; the original filename
also works).

### How it fits together

```
schedule / push / manual
        │
        ▼
   build job ──► fetch live data ──► regenerate enso-dashboard.html
        │            └─► commit HTML + cache back to main ([skip ci])
        │            └─► stage _site/ (index.html, .nojekyll) as artifact
        ▼
  deploy job ──► publish artifact to GitHub Pages
```

A few details that keep it robust:

- **No trigger loops.** The auto-commit message ends with `[skip ci]`, so the
  build committing the regenerated HTML back to `main` doesn't kick off another
  run.
- **`.nojekyll`** is included so Pages serves the files as-is without running
  Jekyll over them.
- **`concurrency: pages`** prevents two deploys from clobbering each other.
- The build still falls back to `data_cache.json` if a source is unreachable, so
  a flaky NOAA endpoint won't publish a broken page.

### Running on your own host instead

If you'd rather not use Pages, any cron host works — the script just writes a
static file you can serve however you like:

```cron
0 6 * * 2,5  cd /path/to/enso-rebuild && /usr/bin/python3 build_dashboard.py
```

## Notes

- The page itself is static and does **not** poll for updates in the browser —
  freshness comes entirely from the scheduled rebuild. (Reloading a static file
  wouldn't change anything between rebuilds.)
- The history charts use whatever the sources provide for the last 36 periods.
  ONI is seasonal (3-month overlapping), MEI is bimonthly; the others are monthly.
- RONI replaced ONI for official monitoring in Feb 2026; the ONI feed still
  publishes, so the card is labelled "ONI → RONI". If/when CPC posts a clean RONI
  data file, add it to `SOURCES` and point the ONI parser at it.
