# Brad DeLong's Weblog Archive

Recovering and organizing Brad DeLong's weblog archive (pre-2005) — a Streamlit app built in UC Berkeley's CDSS Data Discovery practicum. (Repository slug: CDSSDEMO — only the display name changed, the repository address is unchanged.)

**Live demo:** [https://dsdiscovery-pre2005demo.streamlit.app/](https://dsdiscovery-pre2005demo.streamlit.app/)

## The problem

DeLong's Semi-Daily Journal spans decades, but surviving pre-2005 copies are scattered across overlapping backup folders, aggregator index pages, and early static course directories, while many referenced figures were never bundled locally. This project consolidates the archive into a deduplicated, searchable database with verified Wayback Machine provenance and missing-asset recovery.

## What it does

- **Full-Text Search & Corpus Scope Filtering (FTS5):** Search across **11,772** deduplicated documents with scope filters (`Unique Archive`, `Canonical Blog Posts (2001–2005)`, `Early Pages & Courses (1996–2000)`, or `All Raw Files`), cleaned minute-level timestamps, a formatted HTML reader, and pre-verified Wayback Machine CDX snapshot links.
- **Posting Timeline:** Volume of posts by year/month over the deduplicated corpus, eliminating inflated counts from duplicate backup folders (`copy` directories, `July 1 2003` snapshots) and aggregator index pages.
- **Asset Recovery:** Every missing image path is classified as `chart` / `chrome` / `desktop` / `other`, and recoverable figures are queried against the Wayback Machine so they can be rendered back into the article view.

## Data snapshot (deployed V2.1 database, updated 2026-11)

- **21,531 total records** in the `posts` table, deduplicated to **11,772 unique documents** (`is_unique_corpus = 1`, Jan 01, 1990 – Dec 21, 2005):
  - **6,533 canonical blog posts (2001–2005):** 100% matched with Emily Liang's curated Movable Type post inventory (`is_canonical_post = 1`).
  - **5,239 early static pages & course materials (1996–2000):** Deduplicated op-eds, working essays, and UC Berkeley lecture notes.
- **Verified historical provenance:** **6,513** pre-verified Wayback Machine CDX snapshot URLs and **10,587** resolved canonical permalinks.
- **889 missing-asset references:** 379 DeLong charts, 195 third-party chrome, 65 desktop-folder files, 250 other.
- **Wayback image recovery:** 345 snapshots found, 188 with no snapshot, 0 pending — a 54.8% recovery rate over `chart` + `other` references (345 / 629).

These numbers describe the `V2.1` database release, not a live-updating count.

## Run

Requires Python 3.10+.

```bash
pip install -r requirements.txt
streamlit run app.py
```

On first run, the app downloads the compacted corpus database (`weblog_corpus.db`, `V2.1`) from the GitHub Release if it is not already present. The database itself is not stored in this repository (see `.gitignore`).

## Repository layout

```
app.py                          # Streamlit app (FTS5 search, corpus scope filters, timeline, asset recovery)
requirements.txt                # Third-party dependencies (streamlit, pandas)
.gitignore                      # Python, virtualenvs, and the downloaded database
LICENSE                         # MIT License for the code in this repository
scripts/
  classify_missing.py           # Build missing_assets table and classify missing paths (chart / chrome / desktop / other)
  wayback_recover.py            # Query the Wayback Availability API for pending missing assets
  merge_emily_metadata.py       # Align curated 2001–2005 Movable Type metadata, timestamps, and CDX Wayback URLs
  prepare_clean_corpus.py       # Flag unique deduplicated documents (is_unique_corpus) and compact DB via VACUUM
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

# 3. Merge curated 2001–2005 metadata & CDX links, then build deduplicated corpus flags
python scripts/merge_emily_metadata.py
python scripts/prepare_clean_corpus.py
```

## License

The code in this repository is licensed under the MIT License — see `LICENSE`. The DeLong weblog text, images, and corpus database are not covered by this license.

## Data provenance and limits

- Post text, metadata, and images come from DeLong's weblog backups, Emily Liang's curated 2001–2005 Movable Type inventory, and the Internet Archive Wayback Machine; recovered figures and snapshots are linked to Wayback URLs rather than re-hosted here.
- Coverage is pre-2005 only (`1990–2005`). Deduplication combines curated Movable Type canonical post matching (`6,533` posts) with rule-based filtering of backup snapshots and index pages (`5,239` early static/course pages).
