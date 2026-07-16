# Project State

## What This Is
- `journal-finder` is a Claude Code skill for recommending journals to social science and humanities researchers, plus the environmental-health crossover journals those researchers commonly target.
- The current product goal is to match a paper to suitable submission journals using local metadata, Aims & Scope text, recent article titles, and multi-objective ranking.

## Scope / Coverage
- Primary scope: SSCI / AHCI (社科人文全域).
- Secondary scope (added 2026-07): an environmental-health crossover subset drawn from SCIE, to serve climate×health×demography research (the maintainer's own field).
  - Whitelist matches JCR col[31] 分区详情:
    - `PUBLIC, ENVIRONMENTAL & OCCUPATIONAL HEALTH` — net-new kept in full.
    - `ENVIRONMENTAL SCIENCES` — kept only after a journal-name blacklist strips environmental chemistry / ecology / water-treatment / geoscience subfields (see `SCIE_ENV_NAME_BLACKLIST` in `fetch_ssci_journals.py`).
  - ~380 net-new journals added (e.g. Environment International, Environmental Pollution, Urban Climate, Climatic Change, Environmental Research Letters). Out of scope by design: pure environmental engineering/chemistry/ecology.
- Every journal carries `_meta.source_scope` (`ssci_ahci` | `scie_env_health`) for debugging and future filtering.

## Core Data
- Main database: `data/journals_ssci.json` with 5,183 canonical journals (4,802 SSCI/AHCI + 381 SCIE env-health).
- The default build uses only the current `sources_ssci_all.json` whitelist. Legacy economics/demography source files are excluded unless `scripts/build_database.py --include-legacy` is passed explicitly.
- Final merged discipline files still exist for compatibility: `data/journals_economics.json` and `data/journals_demography.json`.
- Manual supplement: `data/manual_supplement.json` has 5,629 entries for JCR quartile, CAS zone, IF, APC, waiver, word limits, and review evidence. Unprovenanced legacy review-type placeholders are kept as unknown rather than presented as verified facts.
- Codex harvest staging: `data/codex_harvest.json` stores publisher scrape output before cleaning and ingestion.
- Semantic assets: `data/journal_index.faiss`, `data/journal_embeddings.npy`, `data/journal_index_map.json`, plus `data/journal_index_meta.json` as the generation checksum/commit marker.
- Scope text:
  - `data/aims_scope.json` holds the current scope store.
  - `data/manual_webfetch_scope_seed.json` holds hand-fetched, Codex-harvested, and WebFetch-condensed high-value scope records that are overlaid into embeddings (now 46 entries, including 29 usable Codex harvest records and 3 Elsevier WebFetch additions).
- A missing-scope worklist can be generated locally from the coverage data when doing manual backfill.

## Current Behavior
- `skill/scripts/query_db.py` now supports `--discipline all` and multi-objective ranking.
- Ranking fields include `_final_score`, `_fit_scores`, `_risk_flags`, `_recommendation_notes`, `_review_confidence`, and `_review_evidence`.
- Recommendation quality now uses concept-coverage lexical matching, calibrated semantic scores, a fixed 300-journal recall pool, a topic-fit admission floor, and topic-dominant final ranking. Study geography/method/sample context is downweighted.
- Clearly identified review/commissioned outlets are excluded by default; `--include-review-only` is the explicit override.
- `scripts/evaluate_recommendations.py` runs the first 8-case real-data Top-K benchmark without loading the model once per case.
- Current canonical coverage snapshot: usable Aims & Scope text >=200 chars: 839/5,183 (16.2%); APC: 1,619 (31.2%); JCR quartile: 3,992 (77.0%); CAS zone: 4,424 (85.4%); CN-author ratio: 3,825 (73.8%); 2024 volume: 4,052 (78.2%); review median: 829 (16.0%).
- Latest Codex harvest ingest added 29 usable Aims & Scope records and 5 APC records, then rebuilt `journals_ssci.json` and the FAISS semantic index.
- `skill/scripts/semantic_search.py` uses SPECTER2 + FAISS when available and falls back gracefully if semantic lookup fails.
- Runtime data discovery supports `--data-dir`, `JOURNAL_FINDER_DATA_DIR`,
  install config, repository-relative discovery, and the old
  `~/journal-finder/data` path only as a final compatibility fallback.
- `scripts/install_skill.py` deploys the skill and records the actual data path;
  `skill/scripts/doctor.py` verifies the database, dependencies, and semantic assets.
- Hard filters are applied inside full-corpus semantic recall and re-applied after union, so restrictive filters do not starve the candidate set and semantic-only candidates cannot bypass constraints.
- `scripts/build_embeddings.py` overlays `manual_webfetch_scope_seed.json` so manually fetched scope text survives later rebuilds. The cache records its schema/model, validates normalized 768-D vectors, prunes journals outside the canonical corpus, and publishes index/map artifacts with a checksum manifest.

## What We Learned
- Springer official Aims & Scope pages are reasonably fetchable with the custom script.
- Wiley and Elsevier often return `403` through plain requests. WebFetch was also fully blocked in this environment (all domains rejected), so the reliable path is: user pastes official scope text → written into `manual_webfetch_scope_seed.json` → merge → rebuild.
- The best long-term strategy is official Aims & Scope first, recent titles and topics as support, and provisional text only as a fallback.
- **Flagship env journals need condensed, health-focused scope.** Journals like Environment International / Environmental Research carry large environmental-chemistry / toxicology sections in their official scope, which dilute the SPECTER2 vector away from epidemiology/climate-health queries. Condensing the seed text to just the epidemiology/climate-health portion moved Environment International from "not in top 60" to rank 4 for a temperature-mortality paper.
- **Do not boost a journal that desk-rejects the paper type.** Environmental Pollution's official scope explicitly returns "air pollution and health" observational/ecological studies without review, so it is intentionally left with full (un-condensed) scope and not surfaced for that kind of paper.

## Important Scripts
- `scripts/fetch_ssci_journals.py` seeds journals from the JCR list. Two intake channels via `classify_scope()`: SSCI/AHCI, plus the environmental-health SCIE whitelist. Reads header at `min_row=2`. Has a Downloads/Desktop/Documents fallback for the moved JCR Excel.
- `scripts/fetch_official_aims_scope.py` fetches publisher Aims & Scope pages and writes failures separately.
- `scripts/merge_manual_webfetch_scope_seed.py` merges the manual seed into `data/aims_scope.json`.
- `scripts/build_embeddings.py` rebuilds embeddings and the FAISS index.
- `scripts/import_excel_data.py` imports local JCR/CAS Excel files with ISSN, exact-title, and conservative fuzzy-title matching, then writes unmatched JCR/CAS lists.
- `scripts/build_database.py` merges `manual_supplement.json` back into `data/journals_ssci.json`; requires valid `_meta.source_scope`, validates the canonical output, and writes it atomically.
- `scripts/codex_harvest.py` writes publisher scrape output to `data/codex_harvest.json`; `scripts/codex_harvest_tandf.mjs` uses Playwright for Taylor & Francis pages.
- `scripts/ingest_codex_harvest.py` cleans `data/codex_harvest.json` into `manual_webfetch_scope_seed.json` and `manual_supplement.json`.
- `docs/publisher_harvest_strategy.md` records what worked for Taylor & Francis and how to handle Elsevier/Wiley/SAGE next.
- `scripts/fetch_metrics.py` / `scripts/fetch_review_times.py` compute the slow metrics (CN ratio, annual volume, review timelines). Both now read `sources_ssci_all.json` (previously only the legacy per-discipline files, which silently skipped SSCI journals) and accept `--scope scie_env_health` to process just one intake channel. Use the real MAILTO + OpenAlex key.

## Environment Notes
- Use the same `python` environment where `requirements.txt` was installed.
- Pass licensed JCR/CAS Excel files with `--jcr-file` and `--cas-file`;
  common document directories are searched only as a convenience fallback.

## Long-Term Focus
- Improve official Aims & Scope coverage.
- Keep Wiley/Elsevier hand-fetched seeds in `manual_webfetch_scope_seed.json`.
- Preserve explainable ranking instead of opaque scoring.
- Keep unknown quartile/review evidence as unknown; do not manufacture defaults.
- Prefer small, additive updates over rewriting the whole pipeline.
