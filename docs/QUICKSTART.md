# Quick Start — Getting Your First Leads

Step-by-step, no tech background assumed. Same flow as hctx-intel.

## Part 1: Create the GitHub repo (5 min)

1. Go to https://github.com/new
2. Repository name: `hctx-developers`
3. Description: `Lead gen scraper for clean fill dirt sales — TX land developers`
4. Set to **Public** (or Private if you prefer)
5. Do NOT initialize with README, .gitignore, or license — we already have those
6. Click **Create repository**

## Part 2: Push the code (5 min)

From your local machine, in the folder where you downloaded this project:

```bash
git init
git add .
git commit -m "Initial commit: Regrid scraper v0.1"
git branch -M main
git remote add origin https://github.com/phillipsmithv/hctx-developers.git
git push -u origin main
```

## Part 3: Add your Regrid token as a secret (3 min)

1. Get your token from https://app.regrid.com/profile/api (copy it)
2. In your GitHub repo, go to **Settings** (top right of repo page)
3. Left sidebar: **Secrets and variables → Actions**
4. Click **New repository secret**
5. Name: `REGRID_TOKEN`
6. Secret: paste your token
7. Click **Add secret**

## Part 4: Run it manually to test (2 min)

1. In your GitHub repo, click the **Actions** tab
2. You'll see "Daily Developer Lead Scrape" in the left sidebar — click it
3. Click **Run workflow** dropdown (right side) → **Run workflow** button
4. Wait ~2-5 minutes for the green checkmark
5. Click into the completed run → scroll to bottom → download the **regrid-leads-XXXX** artifact (it's a zip with your CSV)

## Part 5: Review the CSV

Open the CSV. Each row is a candidate lead with:

- **owner_name** — the LLC that owns the parcel
- **mailing_address** — where to send mail (often the parent developer's office)
- **site_address** — where the actual dirt is
- **gisacre** — acreage of the parcel
- **saledate** — when they bought it (recent = active development)
- **matched_keyword** — which keyword caught it (LLC / Development / etc.)

**What to do with it:**

1. Scan owner_name for repeat patterns (e.g. five different LLCs all mailing to the same Sugar Land address = one parent developer)
2. Pull the hottest 10-20 (largest acreage, most recent sale)
3. LinkedIn-search the LLC name OR mailing address → find VP Land Acquisition / Development Manager
4. Add their direct contact to GHL with the **Fill Dirt Prospect** tag
5. Jarvis runs the text → call/VM → email sequence

## Tuning the filters

If you're getting too many / too few leads, edit these constants in `scrapers/regrid_scraper.py`:

```python
MIN_ACRES = 3.0                  # raise to get bigger sites only
MAX_ACRES = 200.0                # lower if you don't want huge tracts
SALE_DATE_LOOKBACK_MONTHS = 24   # lower for fresher leads only
OWNER_KEYWORDS = [...]           # add/remove keywords
```

Commit and push the changes, and the next daily run picks them up.

## Daily cron

Once set up, the scraper runs automatically every day at **7:00 UTC** (2 AM Houston CST / 1 AM CDT). Same time as hctx-intel so you can check both with morning coffee.

## When something breaks

1. Go to **Actions** tab in your repo
2. Find the most recent failed run (red X)
3. Click in → look at the log for the error
4. Common issues:
   - **401 Unauthorized** → REGRID_TOKEN is wrong or expired. Regenerate at app.regrid.com.
   - **429 Too Many Requests** → rate limit hit. Increase `REQUEST_INTERVAL_SEC` in the scraper.
   - **No features returned** → filters too tight. Widen `MIN_ACRES` or `OWNER_KEYWORDS`.

Paste the error into our next chat and we'll fix it.
