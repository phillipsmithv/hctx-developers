"""
Regrid scraper for hctx-developers (v0.7 — client-side county filter).

What we learned from v0.6 diagnostics:
  - state2=TX filter works
  - geoid filter doesn't work on this account tier
  - county text filter doesn't work on this account tier
  - path parameter expects a specific slug format we couldn't probe
    (the probe timed out trying to fetch real parcel paths)

v0.7 strategy:
  - Filter via fields[state2][eq]=TX + fields[owner][ilike]=KW
    + fields[ll_gisacre][between] (3 fields, well under the 4-field limit)
  - Pull all TX results in 1000-parcel pages
  - Filter to Fort Bend / Harris CLIENT-SIDE using the 'county' value
    that comes back in each parcel record
  - 90-second timeouts to handle big fetches

Cost: Pulls more parcels than strictly needed, but the acreage band (3-200)
and owner keyword filters keep the actual result counts manageable. Stays
well under the 2000/month Pro Trial quota.

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
    raise SystemExit("ERROR: Set REGRID_TOKEN env var")

API_BASE = "https://app.regrid.com/api/v2/parcels/query"

# Client-side filter: keep only parcels whose 'county' field matches these.
# Case-insensitive substring match — Regrid stores county names lowercase.
TARGET_COUNTY_KEYS = ["fort bend", "harris"]

OWNER_KEYWORDS = [
    "LLC", "DEVELOPMENT", "LAND", "HOLDINGS",
    "PARTNERS", "INVESTMENTS", "PROPERTIES", "GROUP",
]

MIN_ACRES = 3.0
MAX_ACRES = 200.0
SALE_DATE_LOOKBACK_MONTHS = 24

REQUEST_INTERVAL_SEC = 0.6
REQUEST_TIMEOUT_SEC = 90  # bumped from 30 — big fetches need more time
MAX_PARCELS_PER_REQUEST_TYPE = 3000  # safety cap before client-side filtering

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
# API helpers
# ---------------------------------------------------------------------------

def make_request(params: dict, action: str = "fetch") -> dict:
    full = dict(params)
    full["token"] = REGRID_TOKEN
    try:
        r = requests.get(API_BASE, params=full, timeout=REQUEST_TIMEOUT_SEC)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        log.warning(f"    HTTP error during {action}: {e}")
        try:
            log.warning(f"    body: {r.text[:300]}")
        except Exception:
            pass
        return {}
    except requests.Timeout:
        log.warning(f"    timeout after {REQUEST_TIMEOUT_SEC}s during {action}")
        return {}
    except Exception as e:
        log.warning(f"    error during {action}: {e}")
        return {}


def build_filters(owner_keyword: str) -> dict:
    return {
        "fields[state2][eq]":          "TX",
        "fields[ll_gisacre][between]": f"[{MIN_ACRES}, {MAX_ACRES}]",
        "fields[owner][ilike]":        owner_keyword,
    }


def get_count(owner_keyword: str) -> int:
    params = build_filters(owner_keyword)
    params["return_count"] = "true"
    data = make_request(params, action=f"count {owner_keyword}")
    return data.get("count", 0)


def fetch_parcels_for_keyword(owner_keyword: str) -> list:
    """Fetch all TX parcels for one owner keyword, with pagination."""
    count = get_count(owner_keyword)
    log.info(f"  TX-wide owner~'{owner_keyword}' count={count}")

    if count == 0:
        return []
    if count > MAX_PARCELS_PER_REQUEST_TYPE:
        log.warning(f"    {count} exceeds safety cap {MAX_PARCELS_PER_REQUEST_TYPE}, "
                    f"skipping. Tighten filters if needed.")
        return []

    all_features = []
    offset_id = 0
    page = 1

    while True:
        params = build_filters(owner_keyword)
        params["limit"] = 1000
        params["offset_id"] = offset_id
        params["return_custom"] = "false"  # skip county-specific fields, smaller payload
        params["return_geometry"] = "true"
        params["return_matched_buildings"] = "false"
        params["return_matched_addresses"] = "false"
        params["return_enhanced_ownership"] = "false"
        params["return_zoning"] = "false"

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
            log.warning(f"    last feature missing 'id', stopping pagination")
            break

        offset_id = last_id
        page += 1
        time.sleep(REQUEST_INTERVAL_SEC)

    return all_features


def is_target_county(feat: dict) -> tuple[bool, str]:
    """Check if a parcel belongs to one of our target counties (client-side filter)."""
    props = feat.get("properties", {})
    fields = props.get("fields", {})
    county = (fields.get("county") or "").lower().strip()
    for target in TARGET_COUNTY_KEYS:
        if target in county:
            # Normalize to title case for output
            return True, target.title()
    return False, ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_all() -> list:
    seen_uuids = set()
    deduped_features = []

    fetched_total = 0
    kept_total = 0

    for kw in OWNER_KEYWORDS:
        features = fetch_parcels_for_keyword(kw)
        fetched_total += len(features)

        kept_this_round = 0
        for f in features:
            in_target, county_name = is_target_county(f)
            if not in_target:
                continue

            props = f.get("properties", {})
            fields = props.get("fields", {})
            uuid = fields.get("ll_uuid") or props.get("ll_uuid")
            if uuid and uuid not in seen_uuids:
                seen_uuids.add(uuid)
                props["_matched_keyword"] = kw
                props["_county"] = county_name
                deduped_features.append(f)
                kept_this_round += 1

        kept_total += kept_this_round
        log.info(f"    -> {kept_this_round} matched target counties (Fort Bend/Harris)")
        time.sleep(REQUEST_INTERVAL_SEC)

    log.info(f"Fetched {fetched_total} parcels across TX, kept {kept_total} unique in target counties")
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
    log.info("hctx-developers Regrid scraper v0.7 starting")
    features = scrape_all()
    if features:
        write_outputs(features)
    else:
        log.warning("No features returned. Check filters, token, and Regrid quota.")
    log.info("Done.")
