# Publisher Harvest Strategy

## Current Result

- `data/codex_harvest.json`: 39 cleaned harvest records from the first `scie_env_health` batch.
- Ingested into `data/manual_webfetch_scope_seed.json`: 29 usable Aims & Scope records from `codex_harvest`.
- Ingested into `data/manual_supplement.json`: 5 APC records from `codex_harvest`.
- Rebuilt canonical `data/journals_ssci.json`: 5,183 journals (4,802 SSCI/AHCI + 381 SCIE env-health).
- Rebuilt semantic artifacts:
  - `data/journal_embeddings.npy`
  - `data/journal_index.faiss`
  - `data/journal_index_map.json`

## What Worked

- Taylor & Francis works well through Playwright Chromium when using:
  `https://www.tandfonline.com/action/journalInformation?journalCode=<code>`
- Elsevier / ScienceDirect is reachable through Codex Web Fetch for some pages, even when local requests/headless Chromium receive 403.
- Springer/Springer Nature sometimes works through requests for APC and short scope text.
- A small number of independent publisher sites work through requests.

## What Failed

- Elsevier / ScienceDirect returned 403 or CPE access-denied pages in local requests/headless Chromium. Codex Web Fetch can still retrieve some pages but may hit 429 rate limits.
- Wiley returned Cloudflare security verification in both requests and Chromium.
- SAGE often returned Cloudflare security verification; a small number of SAGE marketing pages were readable through requests.
- OUP returned Cloudflare verification.
- MDPI, RSC, IOP, and some BMC pages were blocked by robots.txt or bot validation.

## Recommended Next Steps

### Elsevier

Use Codex Web Fetch first for high-value titles, then manual/browser-assisted capture if Web Fetch returns 403/429:

- Environment International
- Environmental Research
- Journal of Environmental Management
- Science of the Total Environment
- The Journal of Climate Change and Health
- One Health
- Preventive Medicine
- Environmental Science & Policy
- Sustainable Futures

Preferred workflow:

1. Try Codex Web Fetch on the ScienceDirect Aims & Scope page.
2. Convert the official page into a condensed, non-verbatim scope summary for `manual_webfetch_scope_seed.json`.
3. If Web Fetch is rate-limited or blocked, open the page in a normal user browser and manually capture the scope.
4. Run:
   `python scripts/merge_manual_webfetch_scope_seed.py`
   `python scripts/build_embeddings.py --batch-size 32`

Avoid automated retry loops against ScienceDirect; local browser automation still receives 403. Current successful Codex Web Fetch additions:

- `0301-4797` Journal of Environmental Management
- `1462-9011` Environmental Science & Policy
- `2666-1888` Sustainable Futures

### Wiley

Wiley is consistently behind Cloudflare in automated contexts. Use the same manual capture path for priority journals:

- Global Change Biology
- GeoHealth
- Indoor Air
- Journal of Environmental Quality
- Journal of Flood Risk Management
- Integrated Environmental Assessment and Management

Potential automation path:

- Use an already authenticated/interactive browser profile outside headless mode.
- Export page text after manual Cloudflare completion.
- Keep a human-in-the-loop checkpoint; do not rely on plain requests.

### SAGE

Try two paths:

1. Official journal URL:
   `https://journals.sagepub.com/home/<code>`
2. SAGE shop page:
   `https://us.sagepub.com/en-us/nam/<slug>/journal<id>`

Some SAGE shop pages expose short journal descriptions and APC values without Cloudflare. Treat short descriptions as low confidence unless they exceed 200 characters and clearly describe journal scope.

### Taylor & Francis

Continue using `scripts/codex_harvest_tandf.mjs`.

The code parser works for common `/toc/<code>/current` URLs. Add explicit overrides when old short URLs are used, as with:

- `1090-3127`: `ipec20`
- `1362-5187`: `iejc20`

### Ingestion

After every harvest batch:

1. `python scripts/ingest_codex_harvest.py`
2. `python scripts/merge_manual_webfetch_scope_seed.py`
3. `python scripts/build_database.py`
4. `python scripts/build_embeddings.py --batch-size 32`

Use `codex_harvest.json` only as a staging file. The durable stores are `manual_webfetch_scope_seed.json` for scope and `manual_supplement.json` for APC.
