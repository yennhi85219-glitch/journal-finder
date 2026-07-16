# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Journal Finder: a Claude Code Skill (`/find-journal`) that recommends academic journals for humanities and social science researchers. The canonical database currently contains 5,183 OpenAlex-resolved journals from the current JCR whitelist: 4,802 SSCI/AHCI journals plus 381 environmental-health crossover journals. Each journal is tagged with `_meta.source_scope` (`ssci_ahci` | `scie_env_health`) and `_meta.source_file`.

**Interpreter:** use the same `python` environment where `requirements.txt` was installed.

Two components:
1. **Data pipeline** (`scripts/`) — Python scripts that fetch journal metadata from OpenAlex and Crossref APIs, import JCR/CAS zoning data from Excel, and merge everything into a local JSON database.
2. **Claude Code Skill** (`skill/`) — Deployed to `~/.claude/skills/find-journal/`. Uses `query_db.py` to pre-filter journals, then Claude does semantic matching and outputs a recommendation table.

## Commands

```bash
# Install dependencies
python -m pip install -r requirements.txt

# Install/update the skill with this repository's actual data path
python scripts/install_skill.py

# Verify the installed database, dependencies, and semantic assets
python ~/.claude/skills/find-journal/scripts/doctor.py --strict

# Full data pipeline (run in order, each step takes minutes to hours).
python scripts/fetch_ssci_journals.py --jcr-file /path/to/jcr.xlsx
python scripts/fetch_metrics.py             # CN author ratio + annual volume (slow; supports --scope scie_env_health)
python scripts/fetch_review_times.py        # Review timelines from Crossref (slow; supports --scope scie_env_health)
python scripts/import_excel_data.py --jcr-file /path/to/jcr.xlsx --cas-file /path/to/cas.xlsx
python scripts/build_database.py            # Merge all sources into final DB (~5 sec)
python scripts/build_embeddings.py          # Rebuild SPECTER2 embeddings + FAISS index

# Query the database directly (used by the Skill)
python skill/scripts/query_db.py --discipline all --keywords "temperature mortality,climate health" --sort prestige --top 10

# Verification
python -m pytest -q
python scripts/evaluate_recommendations.py  # optional real-data/model Top-K benchmark
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
- `build_database.py` uses `sources_ssci_all.json` as the canonical source by default. Legacy economics/demography sources are included only with `--include-legacy` and receive explicit `legacy_*` scope tags.
- `data/manual_supplement.json` is the only manually-curated file. Keyed by ISSN-L. Contains JCR quartile, CAS zone, IF, APC, word limits, review type.
- `data/journals_*.json` are the final merged databases committed to git. Rebuild with `build_database.py` after updating any upstream data. The builder validates identity, source scope, and JCR values before atomically replacing outputs.
- `skill/` mirrors what gets deployed to `~/.claude/skills/find-journal/`.
  `scripts/install_skill.py` copies it and writes `journal-finder-config.json`
  with the repository's actual data path. Runtime resolution also supports
  `--data-dir` and `JOURNAL_FINDER_DATA_DIR`; do not reintroduce a fixed clone path.

## Key Design Decisions

- **Topic matching uses OpenAlex `topic_share.id` filter** for journal discovery (not deprecated `concepts`). Topic IDs are hardcoded per discipline in `fetch_journals.py`.
- **Crossref dates come from the `assertion` array**, not top-level fields. Springer uses natural language dates ("21 June 2024"), Wiley uses ISO format. Many publishers (AEA, Chicago Press) don't provide received/accepted dates at all.
- **Unknown metadata stays unknown.** `N/A` JCR quartiles normalize to `null`; review type is emitted only when its provenance is recorded.
- **`query_db.py` excludes non-submittable publications** (OECD reports, IMF papers, working paper series) via name pattern matching.
- **Recall is topic-only and prefix-stable.** The internal pool is fixed at 300; `--top` controls only output length. Preferences apply after hybrid recall.
- **Topic fit is load-bearing.** Lexical concept coverage and calibrated SPECTER2 scores must clear a relevance floor; metadata cannot compensate for an off-topic journal.
- **Study context is not journal scope.** Common geography, method, and sample-population keywords are downweighted and omitted from the semantic query when stronger scope concepts exist.
- **Review-only/commissioned outlets are excluded by default.** Use `--include-review-only` only for review manuscripts.

## Expanding Coverage

The current seed is the JCR list, filtered by `classify_scope()` in `fetch_ssci_journals.py`. To widen coverage:
1. To add another crossover field (like the env-health subset), extend `classify_scope()` with a new JCR category whitelist. Public-health-style categories can be kept in full; broad natural-science categories (like ENVIRONMENTAL SCIENCES) need a journal-name blacklist to strip irrelevant subfields — see `SCIE_ENV_NAME_BLACKLIST`.
2. Re-run the pipeline (fetch → import_excel → build_database → build_embeddings). Fetch/metrics/review are resumable and skip existing journals; use `--scope <tag>` on the slow metrics scripts to backfill just the new intake channel.
3. Tag new intake channels with a distinct `_source_scope` so they carry through to `_meta.source_scope`.
4. Update `SKILL.md` description and Data Location to reflect the new scope.
