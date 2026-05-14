"""
Regrid scraper for hctx-developers (v0.3 — respects 4-field API limit).

Pulls vacant/development-stage parcels owned by LLC-type entities in
Fort Bend and Harris counties using the Regrid Parcel API v2.

v0.3 fixes:
  - Regrid v2 allows max 4 fields per query. v0.2 used 5 (geoid, gisacre[gte],
    gisacre[lte], saledate[gte], owner[ilike]) and was silently rejected.
  - Now uses 3 fields: geoid[eq], ll_gisacre[between], owner[ilike]
  - saledate recency is applied CLIENT-SIDE during flattening, as a tag
    rather than a hard filter — keeps parcels with NULL saledate (which is
    most of them) instead of dropping them entirely
  - Adds is_recent_sale flag to CSV output for easy filtering in Excel

v0.2 fixes (kept):
  - Correct filter syntax: fields[name][op]=value
  - Use geoid (FIPS) for county filter
  - Fixed response parsing for data.parcels.features and properties.fields
  - count preview before each fetch

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

# Counties — use FIPS geoid (5-digit) for unambiguous filtering.
TARGET_COUNTIES = [
    {"name": "Fort Bend", "geoid": "48157"},
    {"name": "Harris",    "geoid": "48201"},
]

OWNER_KEYWORDS = [
    "LLC", "DEVELOPMENT", "LAND", "HOLDINGS",
    "PARTNERS", "INVESTMENTS", "PROPERTIES", "GROUP",
]

MIN_ACRES = 3.0
MAX_ACRES = 200.0
SALE_DATE_LOOKBACK_MONTHS = 24   # applied client-side now

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

# Computed once for client-side saledate tagging
RECENT_SALE_CUTOFF = datetime.utcnow() - timedelta(days=30 * SALE_DATE_LOOKBACK_MONTHS)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def build_base_filters(geoid: str, owner_keyword: str) -> dict:
    """
    Build the fields[...] filter set for v2 /parcels/query.

    Max 4 fields per Regrid rules. We use exactly 3:
      - geoid (county)
      - ll_gisacre (between min/max — counts as ONE field with two operators)
      - owner (ilike substring match)
    """
    return {
        "fields[geoid][eq]":           geoid,
        "fields[ll_gisacre][between]": f"[{MIN_ACRES}, {MAX_ACRES}]",
        "fields[owner][ilike]":        owner_keyword,
    }


def get_count(county: dict, owner_keyword: str) -> int:
    """Get total parcel count without burning quota (count requests are free)."""
    params = build_base_filters(county["geoid"], owner_keyword)
    params["token"] = REGRID_TOKEN
    params["return_count"] = "true"

    try:
        r = requests.get(API_BASE, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get("count", 0)
    except requests.HTTPError as e:
        log.warning(f"    count request HTTP error: {e}")
        try:
            log.warning(f"    response body: {r.text[:300]}")
        except Exception:
            pass
        return 0
    except Exception as e:
        log.warning(f"    count request error: {e}")
        return 0


def fetch_parcels_for_owner(county: dict, owner_keyword: str) -> list:
    """Fetch all parcels matching one owner keyword in one county, with pagination."""
    count = get_count(county, owner_keyword)
    log.info(f"  [{county['name']}] owner~'{owner_keyword}' count={count}")

    if count == 0:
        return []

    if count > MAX_PARCELS_PER_REQUEST_TYPE:
        log.warning(f"    {count} exceeds safety cap {MAX_PARCELS_PER_REQUEST_TYPE}, "
                    f"skipping this combo. Tighten filters if you want to include it.")
        return []

    all_features = []
    offset_id = 0
    page = 1

    while True:
        params = build_base_filters(county["geoid"], owner_keyword)
        params["token"] = REGRID_TOKEN
        params["limit"] = 1000
        params["offset_id"] = offset_id
        params["return_custom"] = "true"
        params["return_geometry"] = "true"

        log.info(f"    fetching page {page} (offset_id={offset_id})")
        try:
            r = requests.get(API_BASE, params=params, timeout=60)
            r.raise_for_status()
        except requests.HTTPError as e:
            log.warning(f"    HTTP error on page {page}: {e}")
            try:
                log.warning(f"    response body: {r.text[:300]}")
            except Exception:
                pass
            break

        data = r.json()
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
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape_all() -> list:
    """Loop counties × owner keywords. Dedupe by ll_uuid."""
    seen_uuids = set()
    deduped_features = []

    for county in TARGET_COUNTIES:
        log.info(f"==> County: {county['name']} (geoid={county['geoid']})")
        for kw in OWNER_KEYWORDS:
            features = fetch_parcels_for_owner(county, kw)
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
    """Quick centroid estimate from any GeoJSON geometry."""
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
    """Return 'Y'/'N'/'' for whether saledate is within our lookback window."""
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
    """Flatten a Regrid v2 GeoJSON Feature into a CSV row."""
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


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("hctx-developers Regrid scraper v0.3 starting")
    features = scrape_all()
    if features:
        write_outputs(features)
    else:
        log.warning("No features returned. Check filters, token, and Regrid quota.")
    log.info("Done.")
