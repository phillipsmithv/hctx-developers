"""
Regrid scraper for hctx-developers (v0.5 — working production build).

What we learned from v0.4 diagnostics:
  - geoid filter returns 0 (doesn't work on this account/data tier)
  - county text filter returns 0 (same issue)
  - state2=TX + owner ilike works (returned 85,459 parcels for TX/LLC)
  - county scoping must use the `path` parameter (separate from fields[])

v0.5 strategy:
  - Filter via fields[state2][eq]=TX + fields[owner][ilike]=KW + fields[ll_gisacre][between]
  - Scope to county using path=/us/tx/<county_slug> (not a 'field', so doesn't count
    against the 4-field limit)
  - Run a quick startup probe to confirm the county slugs work before scraping

Usage:
    REGRID_TOKEN=xxxx python scrapers/regrid_scraper.py
"""

import os
import json
import csv
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REGRID_TOKEN = os.environ.get("REGRID_TOKEN")
if not REGRID_TOKEN:
    raise SystemExit("ERROR: Set REGRID_TOKEN env var (get from app.regrid.com/profile/api)")

API_BASE = "https://app.regrid.com/api/v2/parcels/query"

# Counties — name + Regrid path slug. We'll auto-detect the working slug at startup
# by trying common variants (with underscore, with hyphen, lowercase).
TARGET_COUNTIES = [
    {"name": "Fort Bend", "slug_candidates": ["fort-bend", "fort_bend", "fortbend"]},
    {"name": "Harris",    "slug_candidates": ["harris"]},
]

OWNER_KEYWORDS = [
    "LLC", "DEVELOPMENT", "LAND", "HOLDINGS",
    "PARTNERS", "INVESTMENTS", "PROPERTIES", "GROUP",
]

MIN_ACRES = 3.0
MAX_ACRES = 200.0
SALE_DATE_LOOKBACK_MONTHS = 24   # applied client-side

REQUEST_INTERVAL_SEC = 0.6
MAX_PARCELS_PER_REQUEST_TYPE = 1500

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("regrid_scraper")

RECENT_SALE_CUTOFF = datetime.utcnow() - timedelta(days=30 * SALE_DATE_LOOKBACK_MONTHS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_request(params: dict, action: str = "fetch") -> dict:
    """Make a Regrid request and return parsed JSON, or {} on failure."""
    full_params = dict(params)
    full_params["token"] = REGRID_TOKEN
    try:
        r = requests.get(API_BASE, params=full_params, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        log.warning(f"    HTTP error during {action}: {e}")
        try:
            log.warning(f"    response body: {r.text[:300]}")
        except Exception:
            pass
        return {}
    except Exception as e:
        log.warning(f"    error during {action}: {e}")
        return {}


def detect_county_slug(county: dict) -> str:
    """
    Try each candidate slug with state2=TX filter and pick the one that
    returns a non-zero count. Returns the working slug, or '' if none work.
    """
    for slug in county["slug_candidates"]:
        path = f"/us/tx/{slug}"
        params = {
            "fields[state2][eq]": "TX",
            "path": path,
            "return_count": "true",
        }
        data = make_request(params, action=f"slug probe '{slug}'")
        count = data.get("count", 0)
        log.info(f"  trying path={path}: count={count}")
        if count > 0:
            return slug
    return ""


def build_filters(state: str, owner_keyword: str) -> dict:
    """3 fields: state2, ll_gisacre between, owner ilike."""
    return {
        "fields[state2][eq]":          state,
        "fields[ll_gisacre][between]": f"[{MIN_ACRES}, {MAX_ACRES}]",
        "fields[owner][ilike]":        owner_keyword,
    }


def get_count(county_path: str, owner_keyword: str) -> int:
    params = build_filters("TX", owner_keyword)
    params["path"] = county_path
    params["return_count"] = "true"
    data = make_request(params, action=f"count {owner_keyword}")
    return data.get("count", 0)


def fetch_parcels(county_path: str, county_name: str, owner_keyword: str) -> list:
    count = get_count(county_path, owner_keyword)
    log.info(f"  [{county_name}] owner~'{owner_keyword}' count={count}")

    if count == 0:
        return []
    if count > MAX_PARCELS_PER_REQUEST_TYPE:
        log.warning(f"    {count} exceeds safety cap {MAX_PARCELS_PER_REQUEST_TYPE}, "
                    f"skipping. Tighten filters to include this combo.")
        return []

    all_features = []
    offset_id = 0
    page = 1

    while True:
        params = build_filters("TX", owner_keyword)
        params["path"] = county_path
        params["limit"] = 1000
        params["offset_id"] = offset_id
        params["return_custom"] = "true"
        params["return_geometry"] = "true"

        log.info(f"    fetching page {page} (offset_id={offset_id})")
        data = make_request(params, action=f"page {page}")
        parcels = data.get("parcels", {})
        features = parcels.get("features", []) if isinstance(parcels, dict) else []

        if not features:
            log.info(f"    no more results")
            break

        all_features.extend(features)
        log.info(f"    +{len(features)} parcels (running total: {len(all_features)})")

        if len(features) < 1000:
            break

        last_id = features[-1].get("id")
        if last_id is None:
            log.warning(f"    last feature missing 'id', can't paginate further")
            break

        offset_id = last_id
        page += 1
        time.sleep(REQUEST_INTERVAL_SEC)

    return all_features


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_all() -> list:
    # Step 1: figure out the correct county slugs
    log.info("Detecting working Regrid path slugs for target counties...")
    working_counties = []
    for c in TARGET_COUNTIES:
        log.info(f"==> Probing {c['name']}")
        slug = detect_county_slug(c)
        if slug:
            path = f"/us/tx/{slug}"
            working_counties.append({"name": c["name"], "path": path})
            log.info(f"    -> using path={path}")
        else:
            log.warning(f"    -> no working slug found for {c['name']}, skipping")
        time.sleep(REQUEST_INTERVAL_SEC)

    if not working_counties:
        log.error("No counties found. Cannot continue.")
        return []

    # Step 2: scrape each county × owner combo
    seen_uuids = set()
    deduped_features = []

    for county in working_counties:
        log.info(f"==> Scraping {county['name']} ({county['path']})")
        for kw in OWNER_KEYWORDS:
            features = fetch_parcels(county["path"], county["name"], kw)
            for f in features:
                props = f.get("properties", {})
                fields = props.get("fields", {})
                uuid = fields.get("ll_uuid") or props.get("ll_uuid")
                if uuid and uuid not in seen_uuids:
                    seen_uuids.add(uuid)
                    props["_matched_keyword"] = kw
                    props["_county"] = county["name"]
                    deduped_features.append(f)
            time.sleep(REQUEST_INTERVAL_SEC)

    log.info(f"Total unique parcels: {len(deduped_features)}")
    return deduped_features


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

LEAD_FIELDS = [
    "ll_uuid", "county", "parcel_id", "owner_name",
    "site_address", "site_city", "site_zip",
    "mailing_address", "mailing_city", "mailing_state", "mailing_zip",
    "gisacre", "land_use_desc", "saledate", "saleprice",
    "is_recent_sale", "matched_keyword", "lat", "lon", "regrid_path",
]


def _centroid_from_geometry(geom: dict):
    if not geom:
        return "", ""
    coords = geom.get("coordinates")
    if not coords:
        return "", ""
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        return coords[1], coords[0]
    return "", ""


def _is_recent_sale(saledate_str: str) -> str:
    if not saledate_str:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            d = datetime.strptime(saledate_str, fmt)
            return "Y" if d >= RECENT_SALE_CUTOFF else "N"
        except ValueError:
            continue
    return ""


def flatten_feature(feat: dict) -> dict:
    props = feat.get("properties", {})
    fields = props.get("fields", {})

    lat = fields.get("lat", "")
    lon = fields.get("lon", "")
    if not lat or not lon:
        lat, lon = _centroid_from_geometry(feat.get("geometry"))

    saledate = fields.get("saledate", "")

    return {
        "ll_uuid":         fields.get("ll_uuid", "") or props.get("ll_uuid", ""),
        "county":          props.get("_county", ""),
        "parcel_id":       fields.get("parcelnumb", ""),
        "owner_name":      fields.get("owner", ""),
        "site_address":    fields.get("address", ""),
        "site_city":       fields.get("scity", "") or fields.get("city", ""),
        "site_zip":        fields.get("szip", ""),
        "mailing_address": fields.get("mailadd", ""),
        "mailing_city":    fields.get("mail_city", ""),
        "mailing_state":   fields.get("mail_state2", ""),
        "mailing_zip":     fields.get("mail_zip", ""),
        "gisacre":         fields.get("ll_gisacre") or fields.get("gisacre", ""),
        "land_use_desc":   fields.get("lbcs_activity_desc", "") or fields.get("usedesc", ""),
        "saledate":        saledate,
        "saleprice":       fields.get("saleprice", ""),
        "is_recent_sale":  _is_recent_sale(saledate),
        "matched_keyword": props.get("_matched_keyword", ""),
        "lat":             lat,
        "lon":             lon,
        "regrid_path":     fields.get("path", "") or props.get("path", ""),
    }


def write_outputs(features: list) -> None:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    raw_path = out_dir / f"regrid_raw_{today}.json"
    with raw_path.open("w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
    log.info(f"Wrote {raw_path}")

    csv_path = out_dir / f"regrid_leads_{today}.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEAD_FIELDS)
        writer.writeheader()
        for feat in features:
            writer.writerow(flatten_feature(feat))
    log.info(f"Wrote {csv_path}")


if __name__ == "__main__":
    log.info("hctx-developers Regrid scraper v0.5 starting")
    features = scrape_all()
    if features:
        write_outputs(features)
    else:
        log.warning("No features returned. Check filters, token, and Regrid quota.")
    log.info("Done.")
