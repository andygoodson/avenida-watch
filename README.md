# Coronado Beach Watch

Live ocean-water bacteria dashboard for three Coronado beach stations, moving
south to north:

1. **Avenida Lunar** — station IB-079 (SiteId 121)
2. **Coronado Lifeguard Tower** — auto-discovered from the county site list
3. **Coronado – North Beach** — station IB-080

A GitHub Actions cron pulls the San Diego County ddPCR Enterococcus readings a
few times a day, commits them to `data/stations.json`, and publishes
`index.html` to GitHub Pages. Open the page from any device; use the **‹ ›**
arrows (or the **← →** keys) to move between stations. The page fetches the
latest data and refreshes itself.

## Layout
- `index.html` — the dashboard; fetches `data/stations.json`, one station shown
  at a time with forward/back navigation, auto-refreshes.
- `fetch.py` — headless Playwright fetcher; discovers each station's county
  SiteId, pulls its Enterococcus series, and merges into the JSON.
- `data/stations.json` — the multi-station data series (seeded; kept current by
  the workflow).
- `.github/workflows/update.yml` — cron fetch + Pages deploy.

## How stations are resolved
`fetch.py` loads one known page (SiteId=121), intercepts the JSON the site
selector uses, and reads the full list of monitoring sites (SiteId + name +
StationID). It resolves the two Coronado stations by name/StationID, then pulls
each station's samples by its SiteId. **Every run logs the full discovered site
list**, so the exact county codes for each station are always visible in the
Actions log. If a station can't be resolved or scraped, its existing readings
are left untouched — the page never goes blank.

## How it stays current
`update.yml` runs every 3 hours (UTC), plus on demand and on push. If the
county blocks the run or changes its schema, the fetch step is skipped and the
last good `stations.json` is redeployed unchanged.

## Run it manually
Actions tab → "Update beach data" → "Run workflow".

## If a station reads empty
Check the run log for the discovered site list (the `SiteId= … StationID= …`
lines). If a Coronado station didn't resolve, adjust its matcher in the
`load_config()` block of `fetch.py` (name tokens or known StationID), or set its
`site_id` directly in `data/stations.json`.

## Standard
Advisory line: 1,413 copies/100ml (ddPCR) = CDPH 104 CFU/100ml culture
equivalent. An advisory lifts only when both the latest sample and the
5-sample geomean fall below the line.

## Legacy
`data/samples.json` (the old single-station file) is retained for reference;
the app now reads `data/stations.json`.
