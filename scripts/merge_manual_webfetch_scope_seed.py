#!/usr/bin/env python3
"""
Merge manually web-fetched official aims/scope records into aims_scope.json.

This lets us keep a small, reviewable seed file for Wiley/Elsevier/manual
captures and safely apply it on top of the larger generated aims_scope.json.
"""

import json
from pathlib import Path


DATA_DIR = Path(__file__).parent.parent / "data"
AIMS_PATH = DATA_DIR / "aims_scope.json"
SEED_PATH = DATA_DIR / "manual_webfetch_scope_seed.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    aims_data = load_json(AIMS_PATH)
    seed_data = load_json(SEED_PATH)

    if not isinstance(aims_data, dict):
        raise SystemExit("aims_scope.json must be a dict keyed by ISSN-L")
    if not isinstance(seed_data, dict):
        raise SystemExit("manual_webfetch_scope_seed.json must be a dict keyed by ISSN-L")

    merged = 0
    for issn_l, patch in seed_data.items():
        record = aims_data.get(issn_l, {})
        if not isinstance(record, dict):
            record = {}
        record.update(patch)
        record.setdefault("abstract_summary", "")
        record.setdefault("recent_titles", [])
        aims_data[issn_l] = record
        merged += 1

    save_json(AIMS_PATH, aims_data)
    print(f"Merged {merged} manual webfetch scope records into {AIMS_PATH}")


if __name__ == "__main__":
    main()
