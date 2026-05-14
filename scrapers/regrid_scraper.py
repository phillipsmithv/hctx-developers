"""
Regrid scraper for hctx-developers (v0.4 — DIAGNOSTIC BUILD).

This version prints the full request URL (with token redacted) and the
raw response body for every Regrid call. The goal is to see exactly
what Regrid is returning so we can fix the filter syntax.

After we identify the issue, we'll go back to a clean v0.5 production build.

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

REGRID_TOKEN = os.environ.get("REGRID_TOKEN")
if not REGRID_TOKEN:
    raise SystemExit("ERROR: Set REGRID_TOKEN env var")

API_BASE = "https://app.regrid.com/api/v2/parcels/query"

# Only test ONE combination in debug mode to save quota
TEST_CASES = [
    # Test 1: just geoid, should return MANY parcels
    {
        "name": "TEST 1: geoid only (should be huge)",
        "params": {
            "fields[geoid][eq]": "48157",  # Fort Bend
        },
    },
    # Test 2: geoid + acreage between
    {
        "name": "TEST 2: geoid + ll_gisacre between",
        "params": {
            "fields[geoid][eq]": "48157",
            "fields[ll_gisacre][between]": "[3, 200]",
        },
    },
    # Test 3: geoid + ll_gisacre as separate gte/lte (treats as 2 fields)
    {
        "name": "TEST 3: geoid + ll_gisacre gte/lte separately",
        "params": {
            "fields[geoid][eq]": "48157",
            "fields[ll_gisacre][gte]": "3",
            "fields[ll_gisacre][lte]": "200",
        },
    },
    # Test 4: geoid + owner ilike
    {
        "name": "TEST 4: geoid + owner ilike LLC",
        "params": {
            "fields[geoid][eq]": "48157",
            "fields[owner][ilike]": "LLC",
        },
    },
    # Test 5: state2 instead of geoid (text field)
    {
        "name": "TEST 5: state2=TX + county=Fort Bend",
        "params": {
            "fields[state2][eq]": "TX",
            "fields[county][ilike]": "Fort Bend",
        },
    },
    # Test 6: state2 + owner — broader test
    {
        "name": "TEST 6: state2=TX + owner ilike LLC",
        "params": {
            "fields[state2][eq]": "TX",
            "fields[owner][ilike]": "LLC",
        },
    },
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("regrid_debug")


def redacted_url(prepared_url: str) -> str:
    """Redact the token in the URL for safe logging."""
    if "token=" in prepared_url:
        parts = prepared_url.split("token=")
        token_and_rest = parts[1]
        if "&" in token_and_rest:
            rest = "&" + token_and_rest.split("&", 1)[1]
        else:
            rest = ""
        return parts[0] + "token=<REDACTED>" + rest
    return prepared_url


def run_test(test: dict) -> None:
    log.info(f"=" * 70)
    log.info(f"{test['name']}")
    log.info(f"=" * 70)

    params = dict(test["params"])
    params["token"] = REGRID_TOKEN
    params["return_count"] = "true"

    # Build a PreparedRequest just so we can see the exact URL
    req = requests.Request("GET", API_BASE, params=params).prepare()
    log.info(f"URL: {redacted_url(req.url)}")

    try:
        r = requests.get(API_BASE, params=params, timeout=30)
        log.info(f"HTTP status: {r.status_code}")
        log.info(f"Response (first 500 chars): {r.text[:500]}")
    except Exception as e:
        log.error(f"Request failed: {e}")

    time.sleep(0.6)


if __name__ == "__main__":
    log.info("Regrid DIAGNOSTIC v0.4 starting")
    log.info("Goal: figure out which filter combos work")
    log.info("")

    for test in TEST_CASES:
        run_test(test)
        log.info("")

    log.info("Done. Read the response bodies above to see what Regrid said.")
