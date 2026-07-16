#!/usr/bin/env python3
"""Install the find-journal skill with a portable data-directory config."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SKILL = ROOT / "skill"
DEFAULT_TARGET = Path.home() / ".claude" / "skills" / "find-journal"


def positive_data_dir(path):
    candidate = Path(path).expanduser().resolve()
    database = candidate / "journals_ssci.json"
    if not database.is_file():
        raise argparse.ArgumentTypeError(
            f"{candidate} does not contain journals_ssci.json"
        )
    return candidate


def install_skill(target, data_dir):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_SKILL, target, dirs_exist_ok=True)
    config = {"version": 1, "data_dir": str(data_dir)}
    config_path = target / "journal-finder-config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path


def main():
    parser = argparse.ArgumentParser(description="Install the find-journal skill")
    parser.add_argument(
        "--target",
        type=lambda value: Path(value).expanduser().resolve(),
        default=DEFAULT_TARGET,
        help="Skill install directory",
    )
    parser.add_argument(
        "--data-dir",
        type=positive_data_dir,
        default=ROOT / "data",
        help="Directory containing journals_ssci.json",
    )
    parser.add_argument(
        "--no-doctor",
        action="store_true",
        help="Skip the post-install health check",
    )
    args = parser.parse_args()

    config_path = install_skill(args.target, args.data_dir)
    print(f"Installed skill: {args.target}")
    print(f"Configured data: {args.data_dir}")
    print(f"Wrote config: {config_path}")

    if not args.no_doctor:
        doctor = args.target / "scripts" / "doctor.py"
        result = subprocess.run([sys.executable, str(doctor)], check=False)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
