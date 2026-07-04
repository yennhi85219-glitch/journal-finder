---
name: find-journal
description: This skill should be used when the user asks to "find a journal", "recommend journals", "where should I submit", "journal suggestions", "帮我找期刊", "期刊推荐", "投稿建议", or mentions submitting a research paper in any social science or humanities field (Economics, Sociology, Political Science, Psychology, Education, Law, Communication, Geography, Management, Demography, Anthropology, Linguistics, etc).
argument-hint: [paste title and abstract, or describe your paper topic]
allowed-tools: [Read, Bash, Grep, Glob]
---

# Journal Finder for Social Sciences & Humanities

Help researchers find suitable academic journals for submission by matching their paper's topic against a curated database of SSCI/AHCI journals covering all social science and humanities disciplines.

## Workflow

When invoked, follow these steps:

### 1. Parse User Input

Extract from the user's message:
- **Title** and **Abstract** (or topic description)
- **Keywords** (explicit or inferred from abstract)
- **Preferences** (if stated): speed priority / prestige priority / budget-limited / CN-friendly

If the user provides insufficient information (no abstract or topic), ask them to provide at least a paragraph describing their paper's content.

### 2. Determine Discipline Mode

Analyze the paper's content:
- **Single discipline**: Paper clearly falls within one field (e.g., pure labor economics, pure demography)
- **Cross-disciplinary**: Paper bridges two fields (e.g., population economics, health economics + demography)

For cross-disciplinary papers, search both discipline databases and identify "bridge journals" that accept interdisciplinary work.

### 3. Query the Database

Run the query script to pre-filter candidates:

```bash
python3 ~/.claude/skills/find-journal/scripts/query_db.py --discipline <economics|demography|both> --keywords "keyword1,keyword2,keyword3" [--max-apc N] [--oa-only] [--sort speed|prestige|cn_friendly]
```

The script returns top 15-20 candidate journals as JSON. Use this as the basis for recommendations.

### 4. Rank and Select

From the candidates, select the best 5-8 journals considering:
- Topic fit (primary criterion)
- User's stated preferences
- Mix of tiers: include 1-2 reach journals (top tier), 3-4 good fits (mid tier), 1-2 safer options (accessible tier)

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

The journal database is at:
- `~/journal-finder/data/journals_economics.json`
- `~/journal-finder/data/journals_demography.json`

Schema reference: `references/data-schema.md`

## Edge Cases

- **Unsupported discipline**: Inform the user that the MVP covers Economics and Demography only. Suggest they describe what aspects of their paper overlap with these fields.
- **No good match**: If topic is too niche, suggest broadening search terms or looking at interdisciplinary journals.
- **Missing data**: When key metrics are unavailable for a journal, note this explicitly rather than guessing.

## Output Language

Match the user's input language. If they write in Chinese, respond in Chinese. If English, respond in English. The table can use Chinese headers regardless.
