"""
Regrid path probe (v0.6 diagnostic).

Asks Regrid for a handful of TX parcels and prints the 'path' field
from each result. This tells us the real Regrid slug format for TX
counties, which we then hardcode in v0.7.

Cost: returns up to 10 parcels — minimal quota burn.
"""

import os
import logging
import requests

REGRID_TOKEN = os.environ.get("REGRID_TOKEN")
if not REGRID_TOKEN:
    raise SystemExit("ERROR: Set REGRID_TOKEN env var")

API_BASE = "https://app.regrid.com/api/v2/parcels/query"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("path_probe")


def probe(name: str, params: dict) -> None:
    log.info("=" * 70)
    log.info(name)
    log.info("=" * 70)

    full_params = dict(params)
    full_params["token"] = REGRID_TOKEN

    try:
        r = requests.get(API_BASE, params=full_params, timeout=30)
        log.info(f"HTTP status: {r.status_code}")
        data = r.json()
    except Exception as e:
        log.error(f"Request failed: {e}")
        return

    parcels = data.get("parcels", {})
    features = parcels.get("features", []) if isinstance(parcels, dict) else []
    log.info(f"Got {len(features)} features")

    for i, feat in enumerate(features[:10]):
        props = feat.get("properties", {})
        fields = props.get("fields", {})
        log.info(
            f"  [{i}] path={fields.get('path') or props.get('path')!r} "
            f"county={fields.get('county')!r} "
            f"city={fields.get('city')!r} "
            f"owner={fields.get('owner', '')[:50]!r}"
        )


if __name__ == "__main__":
    log.info("v0.6 PATH PROBE — find real Regrid TX county slugs")

    # Probe 1: Any 10 TX parcels owned by LLC, look at their paths
    probe("Probe 1: TX + LLC, limit 10", {
        "fields[state2][eq]": "TX",
        "fields[owner][ilike]": "LLC",
        "limit": 10,
        "return_geometry": "false",  # smaller payload
    })

    # Probe 2: TX + LLC with county filter "fort bend" (try various capitalizations
    # via ilike which is case-insensitive substring)
    probe("Probe 2: TX + county ilike 'fort bend' + LLC, limit 10", {
        "fields[state2][eq]": "TX",
        "fields[county][ilike]": "fort bend",
        "fields[owner][ilike]": "LLC",
        "limit": 10,
        "return_geometry": "false",
    })

    # Probe 3: TX + county ilike 'harris' + LLC
    probe("Probe 3: TX + county ilike 'harris' + LLC, limit 10", {
        "fields[state2][eq]": "TX",
        "fields[county][ilike]": "harris",
        "fields[owner][ilike]": "LLC",
        "limit": 10,
        "return_geometry": "false",
    })

    log.info("Done. Look at the 'path=' values to see the real slug format.")
