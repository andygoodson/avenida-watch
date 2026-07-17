# Avenida Lunar Watch

Live ocean-water bacteria dashboard for Avenida Lunar beach (Coronado),
station IB-079. A GitHub Actions cron pulls the San Diego County ddPCR
Enterococcus readings a few times a day, commits them to `data/samples.json`,
and publishes `index.html` to GitHub Pages. Open the page URL from any device;
it fetches the latest data and refreshes itself.

## Layout
- `index.html` — the dashboard (fetches `data/samples.json`, auto-refreshes).
- `fetch.py` — headless Playwright fetcher; merges new readings into the JSON.
- `data/samples.json` — the data series (seeded; kept current by the workflow).
- `.github/workflows/update.yml` — cron fetch + Pages deploy.

## How it stays current
`update.yml` runs every 3 hours (UTC), plus on demand and on push. If the
county blocks the run or changes its schema, the fetch step is skipped and the
last good `samples.json` is redeployed unchanged — the page never goes blank.

## Run it manually
Actions tab → "Update beach data" → "Run workflow".

## If it breaks (empty data)
Check the failed run's log for the `payloads=` / `station_rows=` line. If the
county changed the JSON shape, adjust the filter in `fetch.py` (`scrape()`).

## Standard
Advisory line: 1,413 copies/100ml (ddPCR) = CDPH 104 CFU/100ml culture
equivalent. An advisory lifts only when both the latest sample and the
5-sample geomean fall below the line.
