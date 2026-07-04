# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Journal Finder: a Claude Code Skill (`/find-journal`) that recommends academic journals for humanities and social science researchers. MVP covers Economics and Demography/Population Studies.

Two components:
1. **Data pipeline** (`scripts/`) — Python scripts that fetch journal metadata from OpenAlex and Crossref APIs, import JCR/CAS zoning data from Excel, and merge everything into a local JSON database.
2. **Claude Code Skill** (`skill/`) — Deployed to `~/.claude/skills/find-journal/`. Uses `query_db.py` to pre-filter journals, then Claude does semantic matching and outputs a recommendation table.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Full data pipeline (run in order, each step takes minutes to hours)
python3 scripts/fetch_journals.py          # Pull journal metadata from OpenAlex (~5 min)
python3 scripts/fetch_metrics.py           # Compute CN author ratio + annual volume (~8 hours)
python3 scripts/fetch_review_times.py      # Extract review timelines from Crossref (~13 hours)
python3 scripts/import_excel_data.py       # Import JCR/CAS data from Excel (~10 sec)
python3 scripts/build_database.py          # Merge all sources into final DB (~5 sec)

# Query the database directly (used by the Skill)
python3 skill/scripts/query_db.py --discipline economics --keywords "labor,wage" --sort prestige --top 10
```

## Architecture

```
Data flow:
  OpenAlex API ──→ fetch_journals.py ──→ data/raw/sources_*.json
  OpenAlex API ──→ fetch_metrics.py  ──→ data/raw/computed_metrics.json
  Crossref API ──→ fetch_review_times.py ──→ data/raw/review_times.json
  Excel files  ──→ import_excel_data.py ──→ data/manual_supplement.json (updated in-place)
                                                     │
  All of the above ──→ build_database.py ──→ data/journals_economics.json
                                           ──→ data/journals_demography.json
                                                     │
  User invokes /find-journal ──→ SKILL.md instructs Claude ──→ query_db.py filters DB ──→ Claude ranks & outputs
```

- `data/raw/` is gitignored (regenerable, ~50MB). Scripts are resumable — they save progress incrementally and skip already-processed journals on restart.
- `data/manual_supplement.json` is the only manually-curated file. Keyed by ISSN-L. Contains JCR quartile, CAS zone, IF, APC, word limits, review type.
- `data/journals_*.json` are the final merged databases committed to git. Rebuild with `build_database.py` after updating any upstream data.
- `skill/` mirrors what gets deployed to `~/.claude/skills/find-journal/`. `query_db.py` reads from `~/journal-finder/data/` at runtime.

## Key Design Decisions

- **Topic matching uses OpenAlex `topic_share.id` filter** for journal discovery (not deprecated `concepts`). Topic IDs are hardcoded per discipline in `fetch_journals.py`.
- **Crossref dates come from the `assertion` array**, not top-level fields. Springer uses natural language dates ("21 June 2024"), Wiley uses ISO format. Many publishers (AEA, Chicago Press) don't provide received/accepted dates at all.
- **`query_db.py` excludes non-submittable publications** (OECD reports, IMF papers, working paper series) via name pattern matching.
- **Sorting uses `get_normalized_impact()`** which caps `citedness_2yr` at 15 to prevent outliers from dominating rankings. Manual IF takes priority over OpenAlex citedness.

## Adding New Disciplines

To expand beyond Economics/Demography:
1. Identify relevant OpenAlex topic IDs (query known journals in the field, extract their top topics)
2. Add topic IDs to `fetch_journals.py`
3. Re-run the full pipeline
4. Update `SKILL.md` description to include new discipline triggers
