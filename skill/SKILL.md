---
name: find-journal
description: This skill should be used when the user asks to "find a journal", "recommend journals", "where should I submit", "journal suggestions", "帮我找期刊", "期刊推荐", "投稿建议", or mentions submitting a research paper in any social science or humanities field (Economics, Sociology, Political Science, Psychology, Education, Law, Communication, Geography, Management, Demography, Anthropology, Linguistics, etc), or in environmental-health crossover fields (environmental health, environmental epidemiology, climate and health, temperature-mortality, air pollution and health).
argument-hint: [paste title and abstract, or describe your paper topic]
allowed-tools: [Read, Bash, Grep, Glob, AskUserQuestion]
---

# Journal Finder for Social Sciences & Humanities

Help researchers find suitable academic journals for submission by matching their paper's topic against a canonical local database of ~5,183 OpenAlex-resolved journals from the current JCR whitelist: ~4,802 SSCI/AHCI journals plus 381 environmental-health crossover journals. Pure environmental engineering/chemistry/ecology journals and unverified legacy topic records are excluded from the default database.

## Workflow

When invoked, follow these steps:

### 1. Parse User Input

Extract from the user's message:
- **Title** and **Abstract** (or topic description)
- **Keywords** (explicit or inferred from abstract): prefer 3-6 journal-scope concepts. Do not let country, sample population, or method terms replace the core field/problem keywords.
- **Preferences**: topic fit, prestige, speed, budget, CN-friendly, annual volume/capacity, review-time evidence

**Collecting preferences — always use the AskUserQuestion tool, never free-text prompting.**

Unless the user has *already* stated their priorities explicitly in their message, you MUST call the `AskUserQuestion` tool to let them pick — do not ask "你更看重哪个？" in prose and wait for them to type. Present the choices as a selectable card.

Use these settings for the AskUserQuestion call:
- `header`: `投稿偏好`
- `question`: `你更看重哪些方面？（可多选）`
- `multiSelect`: `true`
- `options` (label + description):
  - `主题贴合` — 优先匹配期刊的领域/scope 契合度（默认最重要）
  - `求档次` — 优先 JCR 分区 / 中科院分区 / 影响因子高的期刊
  - `求快` — 优先审稿周期短、且有可信审稿证据的期刊
  - `预算有限` — 优先低 APC、订阅制、钻石 OA 或有减免的期刊
  - `国人友好` — 优先华人作者占比高的期刊
  - `容量稳` — 优先年发文量中高、录用容量大的期刊

Map the user's selections to the query flags:
- `主题贴合`→`fit`, `求档次`→`prestige`, `求快`→`speed`, `预算有限`→`budget`, `国人友好`→`cn`, `容量稳`→`volume`
- Pass them via `--priorities` (comma-separated). If the user picks a single dominant goal, also set the matching `--sort` (e.g. `求快`→`--sort speed`, `求档次`→`--sort prestige`, `国人友好`→`--sort cn_friendly`); for multiple goals use `--sort balanced` with the combined `--priorities`.

If the user explicitly asks for *separate* rankings (e.g. "分别给我求档次和求快两种"), skip the card and run one query per goal. If they ask to balance multiple goals, use combined priorities.

If the user provides insufficient information (no abstract or topic), ask them to provide at least a paragraph describing their paper's content **before** showing the preferences card.

### 2. Determine Search Mode

Analyze the paper's content:
- **Single discipline**: Paper clearly falls within one field (e.g., pure labor economics, pure demography)
- **Cross-disciplinary**: Paper bridges two fields (e.g., population economics, health economics + demography)

Use the unified SSCI/AHCI database by default. For cross-disciplinary papers, keep broad keywords from each field so the hybrid search can identify bridge journals that accept interdisciplinary work.

### 3. Query the Database

Run the query script to pre-filter candidates:

```bash
python ~/.claude/skills/find-journal/scripts/query_db.py --discipline all --keywords "keyword1,keyword2,keyword3" [--max-apc N] [--oa-only] [--min-quartile Q1|Q2|Q3|Q4] [--sort speed|prestige|cn_friendly|balanced] [--priorities fit,prestige,speed,budget,cn,volume,data] [--max-review-days N] [--require-review-data] [--include-review-only]
```

The script returns up to the requested number of candidates. It uses concept-coverage lexical recall plus calibrated SPECTER2 semantic recall, then applies a topic-fit admission floor before preference ranking. Country, method, and sample-context keywords are downweighted automatically. If the semantic index or dependencies are unavailable, it falls back to lexical matching.

Check the JSON field `query.semantic_search`. If it is `false`, explicitly tell the user this run used keyword matching only and that results are less reliable for cross-disciplinary papers.
Check `query.semantic_status` and `query.semantic_error` for the concrete fallback reason. Do not describe a failed semantic run as hybrid search.
Also check `quality.status`: `limited_matches` and `no_good_match` mean the constraints should be relaxed or the search concepts revised; never fill the table with unrelated journals.

Preference examples:
- Prestige list: `--sort prestige --priorities prestige`
- Speed list with evidence: `--sort speed --priorities speed --require-review-data`
- Balanced prestige + speed: `--sort balanced --priorities prestige,speed`
- Budget-limited: `--priorities budget --max-apc 2500`
- CN-friendly: `--sort cn_friendly --priorities cn`

After recall, the script applies multi-objective ranking and attaches:
- `_final_score`: overall submission-fit score
- `_fit_scores`: topic, prestige, speed, cost, CN author presence, annual volume, and data-completeness scores
- `_semantic_fit_score`: calibrated semantic relevance used during hybrid recall
- `_recommendation_notes`: compact positive signals
- `_risk_flags`: compact caution signals
- `_review_confidence`: `credible`, `limited`, `very_limited`, or `missing`
- `_review_evidence`: median review days, sample count, date coverage, and accept-to-online days

Use these fields to explain why a journal is recommended. When review confidence is `limited`, `very_limited`, or `missing`, clearly treat review time as a weak signal and mention the sample-count limitation.

### 4. Rank and Select

From the candidates, select the best 5-8 journals considering:
- `_final_score` and `_fit_scores`, with topic fit as the primary criterion
- User's stated preferences such as speed, prestige, budget, or CN-friendly fit
- `_risk_flags`, `_review_confidence`, and `_review_evidence`
- Mix of tiers: include 1-2 reach journals (top tier), 3-4 good fits (mid tier), 1-2 safer options (accessible tier)

Do not over-recommend a journal only because it is fast, prestigious, or cheap. If topic fit is weak, flag it as a tradeoff.

### 5. Output Recommendation Table

Present results in this format (Chinese headers):

```
## 推荐期刊

| 期刊名称 | JCR分区 | 中科院分区 | 影响因子 | OA/APC | 国人占比 | 年发文量 | 审稿周期(天) | 避坑提示 |
|----------|---------|-----------|---------|--------|---------|---------|-------------|---------|
| Journal Name (Abbr) | Q1 | 1区 | 9.8 | 订阅制 | 8% | 65 | 120 | — |
```

For fields with no data, display "—" (not "null" or "N/A").

After the table, provide a brief analysis for the top 3 recommendations:
- Why each journal is a good fit for this paper
- Potential concerns (e.g., long review time, high desk reject rate)
- Submission tips specific to that journal

### 6. Additional Context (if relevant)

- If user asks about 交叉学科 matching, explain which journals accept cross-disciplinary work
- If user asks about speed, highlight journals with fastest review times
- If user mentions budget constraints, prioritize Diamond OA or journals with fee waivers
- If user asks about 国人友好度, sort by CN author ratio

## Data Location

The journal database directory is resolved from `--data-dir`,
`JOURNAL_FINDER_DATA_DIR`, the install config, or the source repository. Run
`python ~/.claude/skills/find-journal/scripts/doctor.py` if resolution fails.

The resolved data directory contains:
- `journals_ssci.json` — canonical database, ~5,183 journals (4,802 SSCI/AHCI + 381 environmental-health crossover). Each journal has `_meta.source_scope` and `_meta.source_file`.
- JCR quartile / CAS zone / impact factor coverage is high across the DB; review-time and CN-ratio coverage is sparse, so treat review speed and CN-friendliness as high-value but incomplete evidence
- Review-time coverage is low overall, so review speed should be treated as high-value but sparse evidence
- `journals_economics.json`
- `journals_demography.json`
- `journal_index.faiss` — semantic search index
- `journal_index_map.json` — FAISS index to ISSN-L map
- `journal_index_meta.json` — generation checksums used to reject partial or mismatched semantic assets

Schema reference: `references/data-schema.md`

## Edge Cases

- **Unsupported discipline**: The database covers SSCI/AHCI humanities and social sciences broadly, plus an environmental-health crossover subset (environmental epidemiology, climate and health, air pollution and health). It does NOT cover pure natural sciences — including environmental engineering, environmental chemistry, ecology, or clinical medicine. If the topic is outside scope, explain the limitation and ask for the closest social science, humanities, or environmental-health angle.
- **No good match**: If topic is too niche, suggest broadening search terms or looking at interdisciplinary journals.
- **Missing data**: When key metrics are unavailable for a journal, note this explicitly rather than guessing.
- **Review article**: Clearly identified review/commissioned outlets are excluded by default. Use `--include-review-only` only when the manuscript itself is a review or synthesis article.

## Output Language

Match the user's input language. If they write in Chinese, respond in Chinese. If English, respond in English. The table can use Chinese headers regardless.
