#!/usr/bin/env python3
"""
fetch.py - Pull Enterococcus readings for the Coronado beach stations tracked by
this dashboard from the San Diego County SamplesReport (OutSystems) app and
merge them into data/stations.json. Designed to run in GitHub Actions on a
schedule.

Stations tracked (south -> north):
  1. Avenida Lunar                 (StationID IB-079, SiteId 121)
  2. Coronado Main Lifeguard Tower (StationID EH-050, SiteId 7; pre-2019 IB-070)
  3. Coronado North Beach          (StationID EH-060, SiteId 10; pre-2019 IB-080)

SiteIds verified 2026-07-20 against the county Beach & Bay map ("Find a site"
-> View Sample Data links). KNOWN_SITE_IDS below seeds them directly; the
name-based discovery and probe remain as backstops in case the county
renumbers again.

How discovery works
-------------------
The SamplesReport page loads a JSON "site list" used to populate its site
selector. We load one known page (SiteId=121), intercept every JSON response,
and search them for that site list -- records that pair a SiteId with a station
name/StationID. From it we resolve the SiteId for each target station whose
SiteId we don't already know, matching on the station name / StationID.

Then, for each target SiteId, we load SamplesReport?SiteId=<id>, intercept the
ScreenDataSetGetSamples payload, and pull that station's Enterococcus series.

Safety: if a station can't be resolved or scraped (county blocks the runner IP,
schema changes, etc.), that station's existing readings in stations.json are
left untouched -- the last good data is preserved and the page never goes blank.
The script exits non-zero only if NOTHING could be fetched for ANY station.

Every run logs the full list of stations it discovered (SiteId, StationID,
name) so the exact county codes are always visible in the Actions log.
"""
import json, sys, re, datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "stations.json"
BASE = "https://cosdapps.sandiegocounty.gov/sdbeachinfo/SamplesReport?SiteId={sid}"

# Name-key candidates seen in OutSystems site records.
NAME_KEYS = ("SiteName", "Name", "Label", "BeachName", "Description",
             "StationName", "Title", "DisplayName")


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def load_config():
    """Return the tracked stations from stations.json plus the name/StationID
    matchers used to auto-resolve each station's SiteId."""
    obj = json.loads(DATA.read_text()) if DATA.exists() else {}
    stations = obj.get("stations", [])
    threshold = obj.get("threshold", 1413)
    # key -> (name tokens that must all appear, known StationID or None)
    matchers = {
        "avenida":    (("avenida",), "IB-079"),
        "lifeguard":  (("coronado",), "EH-050"),  # + must contain lifeguard/tower
        "northbeach": (("coronado", "north"), "EH-060"),
    }
    return obj, stations, threshold, matchers


# Verified county SiteIds (see module docstring). Applied before discovery.
KNOWN_SITE_IDS = {
    "avenida":    (121, "IB-079"),
    "lifeguard":  (7,   "EH-050"),
    "northbeach": (10,  "EH-060"),
}


def _grab(r, hits):
    try:
        if "sdbeachinfo" in r.url and "json" in r.headers.get("content-type", ""):
            hits.append((r.url, r.json()))
    except Exception:
        pass


def capture(browser, url, wait=4500):
    """Load url in a fresh page and return every JSON payload seen."""
    hits = []
    pg = browser.new_page()
    pg.on("response", lambda r: _grab(r, hits))
    print(f"Loading {url}", flush=True)
    try:
        pg.goto(url, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(wait)
    finally:
        pg.close()
    return hits


def find_site_list(payloads):
    """Scan captured JSON for records pairing a SiteId with a name/StationID.
    Returns list of {site_id, station_id, name}."""
    found = {}

    def walk(o):
        if isinstance(o, dict):
            if "SiteId" in o:
                nm = next((o[k] for k in NAME_KEYS if o.get(k)), "")
                sid = o.get("StationID") or o.get("StationId") or ""
                try:
                    site_id = int(o["SiteId"])
                except (TypeError, ValueError):
                    site_id = None
                if site_id is not None and (nm or sid):
                    found[(site_id, str(sid))] = {
                        "site_id": site_id, "station_id": str(sid),
                        "name": str(nm)}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for _, pl in payloads:
        walk(pl)
    return list(found.values())


def extract_samples(payloads, want_station=None):
    """Pull (date, value) Enterococcus rows. If want_station is given, keep only
    that StationID; else keep all rows (page is already site-scoped). Also
    returns the StationID observed in the payload."""
    recs = []
    for _, pl in payloads:
        try:
            recs.extend(pl["data"]["List"]["List"])
        except Exception:
            continue

    out, seen_station, seen_name = [], None, ""
    for rec in recs:
        site = rec.get("Site", {}) or {}
        st_id = site.get("StationID", "")
        if st_id:
            seen_station = st_id
        if not seen_name:
            seen_name = next((site[k] for k in NAME_KEYS if site.get(k)), "")
        if want_station and st_id != want_station:
            continue
        param = rec.get("Parameter", {}).get("Label", "")
        if param and param != "Enterococcus":
            continue
        s = rec.get("Sample", {})
        date = str(s.get("SampleDate", ""))[:10]
        res = str(s.get("Result", "")).replace(",", "").strip()
        if len(date) == 10 and res.replace(".", "").isdigit():
            out.append((date, int(float(res))))
    return out, seen_station, seen_name


def resolve_site_id(cfg, matchers, site_list):
    """Resolve a SiteId (and StationID/name) for a station from the site list."""
    key = cfg.get("key", "")
    contains, want_sid = matchers.get(key, ((), None))
    if want_sid:                                    # match by known StationID
        for s in site_list:
            if norm(s["station_id"]) == norm(want_sid) and s["site_id"]:
                return s["site_id"], s["station_id"], s["name"]
    for s in site_list:                             # match by name tokens
        nm = norm(s["name"])
        if contains and all(tok in nm for tok in contains):
            if key == "lifeguard" and not ("lifeguard" in nm or "tower" in nm):
                continue
            return s["site_id"], s["station_id"], s["name"]
    return None, want_sid, None


def merge(station, rows):
    data = {d: v for d, v in station.get("readings", [])}
    before = len(data)
    for d, v in rows:
        data[d] = v
    station["readings"] = [[d, data[d]] for d in sorted(data)]
    return len(data) - before


def match_station(cfg, matchers, station_id, name):
    """Does a (StationID, name) pair from a probed page match this station?"""
    key = cfg.get("key", "")
    contains, want_sid = matchers.get(key, ((), None))
    if want_sid and station_id and norm(station_id) == norm(want_sid):
        return True
    nm = norm(name)
    if not nm or not contains:
        return False
    if not all(tok in nm for tok in contains):
        return False
    if key == "lifeguard" and not ("lifeguard" in nm or "tower" in nm):
        return False
    return True


def probe_site_ids(browser, stations, matchers, page_cache):
    """Fallback: walk SiteIds near the known seed and identify stations from
    their own sample payloads (Site.StationID + name). Resolves any station
    still missing a site_id. Logs every station encountered."""
    unresolved = [s for s in stations if not s.get("site_id")]
    if not unresolved:
        return
    known = {s.get("site_id") for s in stations if s.get("site_id")}
    seed = min(known) if known else 121
    # spiral outward from the seed: 122,120,123,119,... within a sane window
    candidates = []
    for d in range(1, 20):
        for sid in (seed + d, seed - d):
            if sid > 0 and sid not in known:
                candidates.append(sid)
    print(f"PROBE: resolving {[s['name'] for s in unresolved]} by walking "
          f"SiteIds around {seed}...", flush=True)
    for sid in candidates:
        if not any(not s.get("site_id") for s in stations):
            break
        payloads = capture(browser, BASE.format(sid=sid), wait=3000)
        page_cache[sid] = payloads
        rows, st_code, st_name = extract_samples(payloads)
        print(f"  probe SiteId={sid}: StationID={st_code or '?'} "
              f"name='{st_name or '?'}' rows={len(rows)}", flush=True)
        if not (st_code or st_name):
            continue
        for cfg in stations:
            if cfg.get("site_id"):
                continue
            if match_station(cfg, matchers, st_code, st_name):
                cfg["site_id"] = sid
                if st_code and not cfg.get("station_id"):
                    cfg["station_id"] = st_code
                print(f"  RESOLVED {cfg['name']} -> SiteId={sid} "
                      f"StationID={st_code} ('{st_name}')", flush=True)
                break


def main():
    obj, stations, threshold, matchers = load_config()
    if not stations:
        print("ERROR: no stations configured in stations.json", flush=True)
        sys.exit(1)

    # Seed verified SiteIds/StationIDs before any discovery.
    for st in stations:
        known = KNOWN_SITE_IDS.get(st.get("key", ""))
        if known:
            sid, code = known
            st["site_id"] = sid       # authoritative -- corrects stale values
            st["station_id"] = code   # (e.g. pre-2019 IB-080 -> EH-060)

    any_ok = False
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)

        # --- Phase 1: discover the site list from a known page ---
        seed = next((s for s in stations if s.get("site_id")), stations[0])
        seed_sid = seed.get("site_id") or 121
        seed_page = capture(b, BASE.format(sid=seed_sid))
        site_list = find_site_list(seed_page)
        print(f"Discovered {len(site_list)} sites from selector payload.",
              flush=True)
        for s in sorted(site_list, key=lambda x: x["site_id"]):
            print(f"  SiteId={s['site_id']:>4}  {s['station_id']:<8}  "
                  f"{s['name']}", flush=True)
        page_cache = {seed_sid: seed_page}

        # --- Phase 2: resolve missing SiteIds from the discovered site list ---
        for st in stations:
            if st.get("site_id"):
                continue
            sid, res_station, res_name = resolve_site_id(st, matchers, site_list)
            if sid:
                st["site_id"] = sid
                if res_station and not st.get("station_id"):
                    st["station_id"] = res_station
                print(f"Resolved {st['name']} -> SiteId={sid} "
                      f"StationID={st.get('station_id')} ({res_name})",
                      flush=True)

        # --- Phase 2b: probe fallback for anything still unresolved ---
        probe_site_ids(b, stations, matchers, page_cache)

        # --- Phase 3: fetch each resolved station ---
        for st in stations:
            sid = st.get("site_id")
            if not sid:
                print(f"WARN: could not resolve SiteId for {st['name']}; "
                      f"keeping existing readings.", flush=True)
                continue

            payloads = page_cache.get(sid) or capture(b, BASE.format(sid=sid))
            page_cache[sid] = payloads
            rows, seen, _ = extract_samples(
                payloads, want_station=st.get("station_id"))
            if not rows and st.get("station_id"):
                rows, seen, _ = extract_samples(payloads, want_station=None)
            if seen and not st.get("station_id"):
                st["station_id"] = seen

            if rows:
                added = merge(st, rows)
                any_ok = True
                latest = st["readings"][-1]
                print(f"OK {st['name']} (SiteId={sid}): {len(rows)} scraped, "
                      f"{added} new, {len(st['readings'])} total. "
                      f"Latest {latest[0]} = {latest[1]}.", flush=True)
            else:
                print(f"WARN: no rows for {st['name']} (SiteId={sid}); "
                      f"keeping existing readings.", flush=True)

        b.close()

    if not any_ok:
        print("ERROR: nothing fetched for any station. Leaving stations.json "
              "untouched.", flush=True)
        sys.exit(1)

    stations.sort(key=lambda s: s.get("order", 99))
    obj.update({
        "threshold": threshold,
        "updated_utc": dt.datetime.now(dt.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stations": stations,
    })
    DATA.write_text(json.dumps(obj, indent=1))
    print("Wrote data/stations.json", flush=True)


if __name__ == "__main__":
    main()
