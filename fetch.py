#!/usr/bin/env python3
"""
fetch.py - Pull Avenida Lunar (IB-079 / SiteId=121) Enterococcus readings from
the San Diego County SamplesReport (OutSystems) app and merge into
data/samples.json. Designed to run in GitHub Actions on a schedule.

Loads the page headless, intercepts the ScreenDataSetGetSamples JSON response
(all rows), filters to station IB-079 / Enterococcus, and merges with the
existing series. If nothing is scraped (e.g. county blocks the runner IP or the
schema changed), it exits non-zero and does NOT touch samples.json, so the last
good data is preserved.
"""
import json, sys, datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright

SITE_ID = 121
URL = f"https://cosdapps.sandiegocounty.gov/sdbeachinfo/SamplesReport?SiteId={SITE_ID}"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "samples.json"


def scrape():
    hits = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        pg.on("response", lambda r: hits.append(r)
              if "ScreenDataSetGetSamples" in r.url else None)
        print(f"Loading {URL}", flush=True)
        pg.goto(URL, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(4000)
        payloads = []
        for r in hits:
            try:
                payloads.append(r.json())
            except Exception:
                pass
        b.close()

    recs = []
    for pl in payloads:
        try:
            recs.extend(pl["data"]["List"]["List"])
        except Exception:
            continue

    out = []
    for rec in recs:
        if rec.get("Site", {}).get("StationID", "") != "IB-079":
            continue
        s = rec.get("Sample", {})
        param = rec.get("Parameter", {}).get("Label", "")
        if param and param != "Enterococcus":
            continue
        date = str(s.get("SampleDate", ""))[:10]
        res = str(s.get("Result", "")).replace(",", "").strip()
        if len(date) == 10 and res.replace(".", "").isdigit():
            out.append((date, int(float(res))))
    print(f"payloads={len(payloads)} station_rows={len(out)}", flush=True)
    return out


def main():
    rows = scrape()
    if not rows:
        print("ERROR: no rows extracted. Leaving samples.json untouched.",
              flush=True)
        sys.exit(1)

    data = {}
    if DATA.exists():
        obj = json.loads(DATA.read_text())
        for d, v in obj.get("readings", []):
            data[d] = v
    before = len(data)
    for d, v in rows:
        data[d] = v
    added = len(data) - before

    series = [[d, data[d]] for d in sorted(data)]
    out = {
        "station": "IB-079",
        "site_id": SITE_ID,
        "threshold": 1413,
        "updated_utc": dt.datetime.now(dt.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "readings": series,
    }
    DATA.write_text(json.dumps(out, indent=1))
    latest = series[-1]
    print(f"OK: {len(rows)} scraped, {added} new, {len(series)} total. "
          f"Latest {latest[0]} = {latest[1]} copies/100ml.", flush=True)


if __name__ == "__main__":
    main()
