import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime_paths = load_script(
    "portable_runtime_paths",
    ROOT / "skill" / "scripts" / "runtime_paths.py",
)


def write_database(data_dir):
    data_dir.mkdir(parents=True)
    journal = {
        "issn_l": "1234-5678",
        "name": "Journal of Labor Economics",
        "topics": [{"name": "Labor Economics and Employment"}],
        "scope_keywords": ["labor economics", "employment"],
        "jcr_quartile": "Q1",
        "warning_tags": [],
        "notes": "",
    }
    (data_dir / "journals_ssci.json").write_text(
        json.dumps([journal]),
        encoding="utf-8",
    )


def test_repo_relative_discovery_works_outside_home(tmp_path, monkeypatch):
    project = tmp_path / "random-project-name"
    script = project / "skill" / "scripts" / "query.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    write_database(project / "data")
    monkeypatch.delenv("JOURNAL_FINDER_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = runtime_paths.resolve_data_dir(script)

    assert resolved == (project / "data").resolve()


def test_explicit_invalid_data_dir_fails_instead_of_silently_falling_back(
    tmp_path,
):
    with pytest.raises(
        runtime_paths.DataDirectoryError,
        match="required files are missing",
    ):
        runtime_paths.resolve_data_dir(
            ROOT / "skill" / "scripts" / "query_db.py",
            explicit=tmp_path / "missing",
        )


def test_installed_skill_uses_written_config_from_unrelated_directory(tmp_path):
    data_dir = tmp_path / "portable-data"
    write_database(data_dir)
    target = tmp_path / "claude-home" / "skills" / "find-journal"
    install = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "install_skill.py"),
            "--target",
            str(target),
            "--data-dir",
            str(data_dir),
            "--no-doctor",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Installed skill" in install.stdout

    unrelated = tmp_path / "unrelated-working-directory"
    unrelated.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "empty-home")
    result = subprocess.run(
        [
            sys.executable,
            str(target / "scripts" / "query_db.py"),
            "--keywords",
            "labor economics",
            "--top",
            "1",
        ],
        cwd=unrelated,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    output = json.loads(result.stdout)

    assert output["query"]["data_dir"] == str(data_dir.resolve())
    assert output["query"]["semantic_status"] == "fallback"
    assert "Semantic index not found" in output["query"]["semantic_error"]
    assert output["results"][0]["name"] == "Journal of Labor Economics"


def test_doctor_reports_keyword_ready_without_semantic_assets(tmp_path):
    data_dir = tmp_path / "data"
    write_database(data_dir)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "skill" / "scripts" / "doctor.py"),
            "--data-dir",
            str(data_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "READY keyword recommendations available" in result.stdout
    assert "WARN semantic index is not built" in result.stdout
