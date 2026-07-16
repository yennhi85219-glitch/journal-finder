#!/usr/bin/env python3
"""Check whether an installed find-journal skill can serve recommendations."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from runtime_paths import DataDirectoryError, resolve_data_dir


SEMANTIC_ASSETS = (
    "journal_index.faiss",
    "journal_index_map.json",
    "journal_index_meta.json",
)


def main():
    parser = argparse.ArgumentParser(description="Check find-journal installation")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if semantic search is unavailable",
    )
    args = parser.parse_args()

    failures = []
    warnings = []
    try:
        data_dir = resolve_data_dir(
            __file__,
            explicit=args.data_dir,
        )
        journals = json.loads(
            (data_dir / "journals_ssci.json").read_text(encoding="utf-8")
        )
        if not isinstance(journals, list) or not journals:
            failures.append("journals_ssci.json is empty or malformed")
        else:
            print(f"OK database: {len(journals):,} journals at {data_dir}")
    except (DataDirectoryError, OSError, json.JSONDecodeError) as exc:
        failures.append(str(exc))
        data_dir = None

    missing_modules = [
        name
        for name in ("numpy", "faiss", "sentence_transformers")
        if importlib.util.find_spec(name) is None
    ]
    if missing_modules:
        warnings.append(
            "missing semantic dependencies: " + ", ".join(missing_modules)
        )
    else:
        print("OK semantic dependencies")

    if data_dir:
        missing_assets = [
            name for name in SEMANTIC_ASSETS if not (data_dir / name).is_file()
        ]
        if missing_assets:
            warnings.append(
                "semantic index is not built: " + ", ".join(missing_assets)
            )
        elif not missing_modules:
            try:
                from semantic_search import load_index, set_data_dir

                set_data_dir(data_dir)
                index, _ = load_index()
                print(f"OK semantic index: {index.ntotal:,} vectors")
            except Exception as exc:
                warnings.append(f"semantic index validation failed: {exc}")

    for warning in warnings:
        print(f"WARN {warning}")
    for failure in failures:
        print(f"FAIL {failure}")

    if failures or (args.strict and warnings):
        raise SystemExit(1)
    print("READY keyword recommendations available")
    if not warnings:
        print("READY hybrid semantic recommendations available")


if __name__ == "__main__":
    main()
