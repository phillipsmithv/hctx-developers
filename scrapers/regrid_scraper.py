"""
Regrid scraper for hctx-developers.

Pulls vacant/development-stage parcels owned by LLC-type entities in
Fort Bend and Harris counties using the Regrid Parcel API v2.

Usage:
    REGRID_TOKEN=xxxx python scrapers/regrid_scraper.py

Output:
    output/regrid_raw_YYYY-MM-DD.json    (full GeoJSON for debugging)
    output/regrid_leads_YYYY-MM-DD.csv   (flattened lead list)
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

# Counties we care about in v1. Regrid uses FIPS county codes; these are TX.
# Fort Bend = 48157, Harris = 48201
TARGET_COUNTIES = [
    {"name": "Fort Bend", "fips": "48157", "path": "/us/tx/fort-bend"},
    {"name": "Harris",    "fips": "48201", "path": "/us/tx/harris"},
]

# Avatar filters: vacant/transitional land likely being graded for development.
# - Acreage 3-200 (your fill-dirt sweet spot)
# - Owner name contains LLC-type keyword
# - Last sale within 24 months (signals active development)
OWNER_KEYWORDS = [
    "LLC", "DEVELOPMENT", "LAND", "HOLDINGS",
    "PARTNERS", "INVESTMENTS", "PROPERTIES", "GROUP",
]

MIN_ACRES = 3.0
MAX_ACRES = 200.0
SALE_DATE_LOOKBACK_MONTHS = 24

# Rate limiting: Regrid allows ~200 req/min. We'll throttle to 100/min for safety.
REQUEST_INTERVAL_SEC = 0.6

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("regrid_scraper")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def build_query_params(county_path: str, offset_id: int = None) -> dict:
    """
    Build query params for the Regrid v2 parcels/query endpoint.

    We filter on:
      - path (county scope)
      - gisacre between MIN_ACRES and MAX_ACRES
      - saledate >= cutoff date
      - owner contains any of our LLC keywords (OR'd via multiple requests)

    Regrid AND's multiple filter params, so to OR across owner keywords
    we make one request per keyword and dedupe by ll_uuid downstream.
    """
    cutoff = (datetime.utcnow() - timedelta(days=30 * SALE_DATE_LOOKBACK_MONTHS)).strftime("%Y/%m/%d")

    params = {
        "token": REGRID_TOKEN,
        "path": county_path,
        "gisacre[gte]": MIN_ACRES,
        "gisacre[lte]": MAX_ACRES,
        "saledate[gte]": cutoff,
        "limit": 1000,
        "return_custom": "true",
    }
    if offset_id:
        params["offset_id"] = offset_id
    return params


def fetch_parcels_for_owner(county: dict, owner_keyword: str) -> list:
    """Fetch all parcels matching one owner keyword in one county, with pagination."""
    all_features = []
    offset_id = None
    page = 1

    while True:
        params = build_query_params(county["path"], offset_id)
        params["owner[ilike]"] = f"%{owner_keyword}%"

        log.info(f"  [{county['name']}] owner~'{owner_keyword}' page {page}")
        try:
            r = requests.get(API_BASE, params=params, timeout=30)
            r.raise_for_status()
        except requests.HTTPError as e:
            log.warning(f"    HTTP error: {e}. Sleeping 5s and continuing.")
            time.sleep(5)
            break

        data = r.json()
        features = data.get("parcels", {}).get("features", []) if isinstance(data.get("parcels"), dict) else data.get("features", [])

        if not features:
            log.info(f"    no more results")
            break

        all_features.extend(features)
        log.info(f"    +{len(features)} parcels (running total: {len(all_features)})")

        # Pagination: if we got less than the limit, we're done.
        if len(features) < 1000:
            break

        # Otherwise grab the last parcel's id for next page.
        last_id = features[-1].get("properties", {}).get("id")
        if not last_id:
            break
        offset_id = last_id
        page += 1
        time.sleep(REQUEST_INTERVAL_SEC)

    return all_features


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape_all() -> list:
    """Loop over counties × owner keywords. Dedupe by ll_uuid."""
    seen_uuids = set()
    deduped_features = []

    for county in TARGET_COUNTIES:
        log.info(f"==> County: {county['name']} ({county['path']})")
        for kw in OWNER_KEYWORDS:
            features = fetch_parcels_for_owner(county, kw)
            for f in features:
                uuid = f.get("properties", {}).get("ll_uuid")
                if uuid and uuid not in seen_uuids:
                    seen_uuids.add(uuid)
                    f["properties"]["_matched_keyword"] = kw
                    f["properties"]["_county"] = county["name"]
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
    "matched_keyword", "lat", "lon",
]


def flatten_feature(feat: dict) -> dict:
    """Flatten a Regrid GeoJSON Feature into a CSV-friendly row."""
    p = feat.get("properties", {})
    fields = p.get("fields", p)  # v2 sometimes nests under 'fields'

    # Centroid for lat/lon (rough — actual centroid calc would use shapely)
    geom = feat.get("geometry", {})
    lat = lon = ""
    if geom.get("type") == "Point":
        lon, lat = geom["coordinates"]
    elif geom.get("coordinates"):
        # Just grab first coord for now; we'll do proper centroids in enrichment layer
        coords = geom["coordinates"]
        while isinstance(coords, list) and coords and isinstance(coords[0], list):
            coords = coords[0]
        if len(coords) == 2:
            lon, lat = coords

    return {
        "ll_uuid":           fields.get("ll_uuid", ""),
        "county":            p.get("_county", ""),
        "parcel_id":         fields.get("parcelnumb", ""),
        "owner_name":        fields.get("owner", ""),
        "site_address":      fields.get("address", ""),
        "site_city":         fields.get("scity", "") or fields.get("city", ""),
        "site_zip":          fields.get("szip", ""),
        "mailing_address":   fields.get("mailadd", ""),
        "mailing_city":      fields.get("mail_city", ""),
        "mailing_state":     fields.get("mail_state2", ""),
        "mailing_zip":       fields.get("mail_zip", ""),
        "gisacre":           fields.get("gisacre", ""),
        "land_use_desc":     fields.get("lbcs_activity_desc", "") or fields.get("usedesc", ""),
        "saledate":          fields.get("saledate", ""),
        "saleprice":         fields.get("saleprice", ""),
        "matched_keyword":   p.get("_matched_keyword", ""),
        "lat":               lat,
        "lon":               lon,
    }


def write_outputs(features: list) -> None:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    # Raw GeoJSON for debugging / map rendering later
    raw_path = out_dir / f"regrid_raw_{today}.json"
    with raw_path.open("w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
    log.info(f"Wrote {raw_path}")

    # Flat CSV for manual review
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
    log.info("hctx-developers Regrid scraper v0.1 starting")
    features = scrape_all()
    if features:
        write_outputs(features)
    else:
        log.warning("No features returned. Check filters and token.")
    log.info("Done.")
