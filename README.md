# hctx-developers

Lead generation scraper for clean fill dirt sales — targets active land developers in Fort Bend and Harris counties, Texas.

Sister project to [hctx-intel](https://github.com/phillipsmithv/hctx-intel) (motivated seller scraper). Same architecture, different ICP.

## What it does

Every day at 7 UTC, this scraper:

1. **Pulls vacant/development-stage parcels** from Regrid's Parcel API v2 for Fort Bend and Harris counties
2. **Filters** for the fill-dirt customer avatar: 3-200 acres, owned by LLC-type entities (LLC, Development, Land, Holdings, Partners, Investments, Properties, Group), sold within the last 24 months
3. **Outputs** a CSV of leads with owner name, mailing address, parcel info, and acreage
4. **Future**: enriches with TX SOS data, scores leads, pushes to GHL with Jarvis sequence

## The customer avatar

Based on Phillip's first lead — Legacy At Harvest Green LLC, owner of a ~10-acre tract at 18502 W Bellfort St, Richmond TX 77407 (Parcel R478970), actively being graded for development.

That avatar = a development LLC sitting on raw acreage that's getting cleared/graded for vertical construction. These customers need clean fill, typically in 100-10,000 yard volumes.

## Setup

### 1. Get your Regrid API token

Log in to [app.regrid.com/profile/api](https://app.regrid.com/profile/api) and generate a token. Pro plan required.

### 2. Local run

```bash
git clone https://github.com/phillipsmithv/hctx-developers.git
cd hctx-developers
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export REGRID_TOKEN="your_token_here"
python scrapers/regrid_scraper.py
```

Output lands in `output/regrid_leads_YYYY-MM-DD.csv`.

### 3. GitHub Actions (daily auto-run)

In the repo settings → Secrets and variables → Actions, add a secret named `REGRID_TOKEN` with your token value.

The workflow at `.github/workflows/daily_scrape.yml` runs at 7 UTC daily and uploads the CSV as a workflow artifact (downloadable from the Actions tab for 30 days).

## Architecture

```
hctx-developers/
├── scrapers/
│   └── regrid_scraper.py        # v1 — Regrid Parcel API
├── enrichment/                  # v2 — TX SOS lookup, registered agent clustering
├── scoring/                     # v2 — lead score 0-100
├── output/                      # daily CSVs + raw GeoJSON
├── config/                      # filter configs (future)
├── .github/workflows/
│   └── daily_scrape.yml         # cron 7 UTC
└── requirements.txt
```

## Roadmap

**v1 (this commit)** — Regrid scraper → CSV → manual review
- [x] Regrid v2 query with owner + acreage + sale date filters
- [x] Fort Bend + Harris counties
- [x] Daily GitHub Actions cron
- [x] CSV output with owner mailing address

**v2 (next)** — Enrichment + scoring
- [ ] TX SOS / Comptroller LLC lookup (registered agent, officers)
- [ ] Cluster LLCs by shared registered agent (catches Johnson Development, Land Tejas, Meritage LLC families)
- [ ] Lead score 0-100 (acreage × recency × parent developer match)
- [ ] TCEQ Stormwater NOI cross-reference (active grading signal)

**v3** — Distribution
- [ ] GitHub Pages dashboard (`phillipsmithv.github.io/hctx-developers`)
- [ ] GHL webhook → auto-tag "Fill Dirt Prospect" → Jarvis sequence
- [ ] Map view with parcel polygons (Leaflet + Regrid tiles)

**v4** — Expansion
- [ ] Montgomery, Brazoria, Galveston, Waller, Liberty, Chambers counties
- [ ] City of Houston / Sugar Land / Richmond grading permit feeds
- [ ] LinkedIn Sales Navigator skip-trace integration

## Notes

- Regrid bills by parcels returned, not requests. Tight filters keep usage low (~100-500 parcels/month estimated for FB+Harris with v1 filters).
- Rate limit: 200 req/min, 10 simultaneous. Scraper throttles to ~100 req/min for safety.
- Filters are tunable in `scrapers/regrid_scraper.py` — adjust `MIN_ACRES`, `MAX_ACRES`, `OWNER_KEYWORDS`, `SALE_DATE_LOOKBACK_MONTHS` and re-run.
