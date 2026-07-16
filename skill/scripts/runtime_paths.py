#!/usr/bin/env python3
"""Resolve journal-finder's data directory across source and installed layouts."""

import json
import os
from pathlib import Path


CONFIG_NAME = "journal-finder-config.json"
DEFAULT_REQUIRED_FILES = ("journals_ssci.json",)


class DataDirectoryError(RuntimeError):
    """Raised when no usable journal data directory can be found."""


def _validate_data_dir(path, source, required_files):
    candidate = Path(path).expanduser().resolve()
    missing = [name for name in required_files if not (candidate / name).is_file()]
    if missing:
        raise DataDirectoryError(
            f"{source} points to {candidate}, but required files are missing: "
            f"{', '.join(missing)}"
        )
    return candidate


def _load_config(script_path):
    script_dir = Path(script_path).resolve().parent
    for directory in (script_dir, script_dir.parent):
        config_path = directory / CONFIG_NAME
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataDirectoryError(
                f"Cannot read install config {config_path}: {exc}"
            ) from exc
        data_dir = config.get("data_dir")
        if not data_dir:
            raise DataDirectoryError(
                f"Install config {config_path} does not contain data_dir"
            )
        return data_dir, config_path
    return None, None


def resolve_data_dir(
    script_path,
    *,
    explicit=None,
    required_files=DEFAULT_REQUIRED_FILES,
):
    """Resolve data with explicit settings first, then portable auto-discovery."""
    if explicit:
        return _validate_data_dir(explicit, "--data-dir", required_files)

    env_path = os.environ.get("JOURNAL_FINDER_DATA_DIR")
    if env_path:
        return _validate_data_dir(
            env_path,
            "JOURNAL_FINDER_DATA_DIR",
            required_files,
        )

    config_path, config_file = _load_config(script_path)
    if config_path:
        return _validate_data_dir(
            config_path,
            f"install config {config_file}",
            required_files,
        )

    candidates = []
    resolved_script = Path(script_path).resolve()
    for parent in resolved_script.parents:
        candidates.append(parent / "data")
    cwd = Path.cwd().resolve()
    candidates.extend(parent / "data" for parent in (cwd, *cwd.parents))
    candidates.append(Path.home() / "journal-finder" / "data")

    searched = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        searched.append(candidate)
        if all((candidate / name).is_file() for name in required_files):
            return candidate

    search_text = "\n  - ".join(str(path) for path in searched)
    raise DataDirectoryError(
        "Cannot find journal-finder data. Set --data-dir, set "
        "JOURNAL_FINDER_DATA_DIR, or reinstall the skill.\n"
        f"Searched:\n  - {search_text}"
    )
