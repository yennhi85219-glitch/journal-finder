#!/usr/bin/env python
"""
Clean and ingest data/codex_harvest.json into the project data stores.

Inputs:
  data/codex_harvest.json

Outputs updated:
  data/manual_webfetch_scope_seed.json
  data/manual_supplement.json

This script intentionally does not write journals_ssci.json or aims_scope.json.
Run merge_manual_webfetch_scope_seed.py and build_database.py after ingestion.
"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HARVEST_PATH = DATA_DIR / "codex_harvest.json"
WORKLIST_PATH = DATA_DIR / "codex_worklist.json"
SEED_PATH = DATA_DIR / "manual_webfetch_scope_seed.json"
SUPPLEMENT_PATH = DATA_DIR / "manual_supplement.json"

MIN_SCOPE_CHARS = 200
APC_MIN = 100
APC_MAX = 15000

BAD_SCOPE_PATTERNS = re.compile(
    r"(performing security verification|access denied|just a moment|"
    r"please confirm you are a human|journal metrics|citescore|impact factor)",
    re.I,
)


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def clean_scope(text):
    text = (text or "").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    return text


def scope_is_usable(text):
    text = clean_scope(text)
    if len(text) < MIN_SCOPE_CHARS:
        return False
    if BAD_SCOPE_PATTERNS.search(text):
        return False
    return True


def apc_is_valid(value):
    if value is None:
        return False
    try:
        value = int(value)
    except (TypeError, ValueError):
        return False
    return APC_MIN <= value <= APC_MAX


def main():
    harvest = load_json(HARVEST_PATH, {})
    worklist = load_json(WORKLIST_PATH, [])
    seed = load_json(SEED_PATH, {})
    supplement = load_json(SUPPLEMENT_PATH, {})

    worklist_by_issn = {
        item.get("issn_l"): item
        for item in worklist
        if item.get("issn_l")
    }

    scope_added = 0
    scope_skipped = 0
    apc_added = 0
    apc_skipped = 0

    for issn_l, record in harvest.items():
        if not isinstance(record, dict):
            continue

        item = worklist_by_issn.get(issn_l, {})
        scope = clean_scope(record.get("aims_scope", ""))
        source_url = record.get("source_url") or ""
        last_checked = record.get("last_checked") or "2026-07-14"
        confidence = record.get("confidence") or "medium"

        if scope_is_usable(scope):
            existing = seed.get(issn_l, {})
            existing_scope = clean_scope(existing.get("aims_scope", "")) if isinstance(existing, dict) else ""
            if len(scope) >= len(existing_scope):
                seed[issn_l] = {
                    "aims_scope": scope,
                    "source": "codex_harvest",
                    "source_url": source_url,
                    "confidence": confidence,
                    "last_checked": last_checked,
                    "journal_name": item.get("name"),
                    "publisher": item.get("publisher"),
                    "source_scope": item.get("source_scope"),
                }
                scope_added += 1
        elif scope:
            scope_skipped += 1

        if "apc_usd" in record:
            apc = record.get("apc_usd")
            if apc_is_valid(apc):
                entry = supplement.setdefault(issn_l, {})
                existing_apc = entry.get("apc_usd")
                if existing_apc in (None, "", 0):
                    entry["apc_usd"] = int(apc)
                    entry["apc_source"] = "codex_harvest"
                    entry["apc_source_url"] = source_url
                    entry["apc_last_checked"] = last_checked
                    entry.setdefault("last_verified", last_checked[:7])
                    apc_added += 1
                else:
                    apc_skipped += 1
            else:
                apc_skipped += 1

    save_json(SEED_PATH, seed)
    save_json(SUPPLEMENT_PATH, supplement)

    print(
        json.dumps(
            {
                "harvest_records": len(harvest),
                "scope_added_or_updated": scope_added,
                "scope_skipped_short_or_suspicious": scope_skipped,
                "apc_added": apc_added,
                "apc_skipped": apc_skipped,
                "manual_webfetch_scope_seed_total": len(seed),
                "manual_supplement_total": len(supplement),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
