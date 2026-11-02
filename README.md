# CDSSDEMO

A Streamlit demo for browsing Brad DeLong's weblog archive (1995–2005), built for the CDSS Data Discovery project on recovering and organizing DeLong's weblog archives.

## What it does

- `app.py` is the Streamlit app: browse and search archived posts, read them in a formatted HTML reader, and check recovered figures via Wayback Machine links.
- On first run, the app downloads the corpus database (`weblog_corpus.db`, 100 MB+) from the V2.0 GitHub Release if it is not already present. The database itself is not stored in this repository.
- `scripts/` contains the missing-asset pipeline that feeds the app's resource-health views: classify missing images/assets, then recover what is available from the Wayback Machine.

## Run

Requires Python 3.10+.

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Repository layout

```
app.py                          # Streamlit app
requirements.txt                # Third-party dependencies (streamlit, pandas)
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
