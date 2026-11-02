# CDSSDEMO

Recovering and organizing Brad DeLong's weblog archive (pre-2005) — a Streamlit app built in UC Berkeley's CDSS Data Discovery practicum.

**Live demo:** https://dsdiscovery-pre2005demo.streamlit.app/

## The problem

DeLong's Semi-Daily Journal spans decades, but the surviving copies are scattered across backup folders, and many of the images and figures referenced in the posts were never bundled locally. This project turns that messy archive into something browsable: full-text search over the posts, a deduplicated publishing timeline, and a triage of every missing image reference with a Wayback Machine recovery attempt.

## What it does

- **Full-text search (FTS5):** search the archived posts and read them in a formatted HTML reader, with a Wayback link for each post.
- **Posting Timeline:** volume of posts by year/month, with a deduplicated view that merges copies of the same post across backup folders and excludes monthly, weekly, category and index pages.
- **Asset Recovery:** every missing image path is classified as chart / chrome / desktop / other, and recoverable figures are queried against the Wayback Machine so they can be rendered back into the article view.

## Data snapshot (deployed V2.0 database, read 2026-11-01)

- **21,531 records** in the posts table, **7,418 unique posts** after deduplication (Jan 01, 1990 – Dec 21, 2005).
- **889 missing-asset references:** 379 DeLong charts, 195 third-party chrome, 65 desktop-folder files, 250 other.
- **Wayback recovery:** 345 snapshots found, 188 with no snapshot, 0 pending — a 54.8% recovery rate over chart + other references (345 / 629).

These numbers describe the V2.0 database release, not a live-updating count.

## Run

Requires Python 3.10+.

```bash
pip install -r requirements.txt
streamlit run app.py
```

On first run, the app downloads the corpus database (`weblog_corpus.db`, 100 MB+) from the V2.0 GitHub Release if it is not already present. The database itself is not stored in this repository (see `.gitignore`).

## Repository layout

```
app.py                          # Streamlit app (search, timeline, asset recovery)
requirements.txt                # Third-party dependencies (streamlit, pandas)
.gitignore                      # Python, virtualenvs, and the downloaded database
scripts/
  classify_missing.py         # Build the missing_assets table and classify each missing path
                              # (chart / chrome / desktop / other)
  wayback_recover.py          # Query the Wayback Availability API for pending missing assets
```

## Scripts

The scripts expect `weblog_corpus.db` in the working directory (the same file the app downloads on first run).

```bash
# 1. Classify missing assets from the posts table into missing_assets
python scripts/classify_missing.py

# 2. Query Wayback for everything still pending (rate-limited and resumable)
python scripts/wayback_recover.py

# Options: --limit N to cap the run, --sleep SECONDS between requests,
# --retry-errors to also retry rows in an error state
```

## Data provenance and limits

- Post text and images come from DeLong's weblog backups and the Wayback Machine; recovered figures are linked to their Wayback snapshots, not re-hosted here.
- Coverage is pre-2005 only, and the timeline's deduplication merges copies by normalized title + date, so the unique-post count is an approximation, not a hand-verified census.
