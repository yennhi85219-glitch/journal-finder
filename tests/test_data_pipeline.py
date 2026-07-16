import importlib.util
import hashlib
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_database = load_script("build_database", "scripts/build_database.py")
build_embeddings = load_script("build_embeddings", "scripts/build_embeddings.py")
import_excel_data = load_script("import_excel_data", "scripts/import_excel_data.py")
semantic_search = load_script("semantic_search", "skill/scripts/semantic_search.py")


def source_record(issn_l, name, topics=None, source_scope=None):
    record = {
        "issn_l": issn_l,
        "name": name,
        "alternate_titles": [],
        "topics": topics or [],
    }
    if source_scope:
        record["_source_scope"] = source_scope
    return record


def test_merge_journal_is_deterministic_and_drops_unverified_placeholders():
    source = source_record(
        "1234-5678",
        "Test Journal",
        topics=[
            {"name": "Zeta", "count": 1},
            {"name": "Alpha", "count": 2},
            {"name": "Zeta", "count": 3},
        ],
    )
    manual = {
        "1234-5678": {
            "jcr_quartile": " N/A ",
            "review_type": "double_blind",
        }
    }

    journal = build_database.merge_journal(source, {}, {}, manual)

    assert journal["scope_keywords"] == ["alpha", "zeta"]
    assert journal["jcr_quartile"] is None
    assert journal["review_type"] is None


def test_merge_journal_keeps_review_type_with_provenance():
    source = source_record("1234-5678", "Test Journal")
    manual = {
        "1234-5678": {
            "review_type": "Double Blind",
            "review_type_source": "publisher_page",
        }
    }

    journal = build_database.merge_journal(source, {}, {}, manual)

    assert journal["review_type"] == "double_blind"


def test_supplement_migration_clears_old_defaults():
    entry = {
        "jcr_quartile": "",
        "review_type": "double_blind",
    }

    import_excel_data.normalize_supplement_entry(entry)

    assert entry["jcr_quartile"] is None
    assert entry["review_type"] is None


def test_import_jcr_starts_at_second_row_and_normalizes_na(monkeypatch):
    row = [None] * 32
    row[1] = "First Data Journal"
    row[3] = "1234-5678"
    row[7] = "N/A"
    row[8] = " N/A "
    calls = {}

    class FakeSheet:
        def iter_rows(self, min_row, values_only):
            calls["min_row"] = min_row
            calls["values_only"] = values_only
            return [tuple(row)]

    class FakeWorkbook:
        sheetnames = ["JCR"]

        def __getitem__(self, name):
            assert name == "JCR"
            return FakeSheet()

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(
        import_excel_data.openpyxl,
        "load_workbook",
        lambda *args, **kwargs: FakeWorkbook(),
    )

    result = import_excel_data.import_jcr()

    assert calls == {"min_row": 2, "values_only": True, "closed": True}
    assert result["1234-5678"]["jcr_quartile"] is None


def test_unified_sources_are_canonical_and_legacy_is_explicit(tmp_path, monkeypatch):
    unified = [
        source_record(
            "1111-1111",
            "Canonical Journal",
            source_scope="ssci_ahci",
        ),
    ]
    economics = [
        source_record("1111-1111", "Duplicate Legacy Journal"),
        source_record(
            "2222-2222",
            "Economics Legacy Journal",
            source_scope="ssci_ahci",
        ),
    ]
    demography = [
        source_record("3333-3333", "Demography Legacy Journal"),
    ]
    for filename, records in [
        ("sources_ssci_all.json", unified),
        ("sources_economics.json", economics),
        ("sources_demography.json", demography),
    ]:
        (tmp_path / filename).write_text(json.dumps(records), encoding="utf-8")

    monkeypatch.setattr(build_database, "RAW_DIR", tmp_path)

    canonical = build_database.load_sources()
    with_legacy = build_database.load_sources(include_legacy=True)

    assert [item["issn_l"] for item in canonical] == ["1111-1111"]
    assert canonical[0]["_source_scope"] == "ssci_ahci"
    assert canonical[0]["_source_file"] == "sources_ssci_all.json"
    assert [item["issn_l"] for item in with_legacy] == [
        "1111-1111",
        "2222-2222",
        "3333-3333",
    ]
    assert with_legacy[1]["_source_scope"] == "legacy_economics"
    assert with_legacy[2]["_source_scope"] == "legacy_demography"


def test_unified_source_without_explicit_scope_is_rejected(tmp_path, monkeypatch):
    records = [source_record("1111-1111", "Unscoped Journal")]
    (tmp_path / "sources_ssci_all.json").write_text(
        json.dumps(records),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_database, "RAW_DIR", tmp_path)

    with pytest.raises(ValueError, match="missing or invalid _source_scope"):
        build_database.load_sources()


def test_missing_canonical_source_requires_explicit_legacy_mode(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "sources_economics.json").write_text(
        json.dumps([source_record("1111-1111", "Legacy Journal")]),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_database, "RAW_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="Canonical source file not found"):
        build_database.load_sources()

    legacy = build_database.load_sources(include_legacy=True)
    assert [item["issn_l"] for item in legacy] == ["1111-1111"]
    assert legacy[0]["_source_scope"] == "legacy_economics"


def test_validate_journals_rejects_duplicates_and_invalid_quartiles():
    valid = {
        "issn_l": "1234-5678",
        "name": "Valid Journal",
        "jcr_quartile": "Q1",
        "_meta": {"source_scope": "ssci_ahci"},
    }
    build_database.validate_journals([valid], "valid")

    duplicate = dict(valid)
    invalid = {
        "issn_l": "9999-9999",
        "name": "Invalid Journal",
        "jcr_quartile": "N/A",
        "_meta": {"source_scope": "ssci_ahci"},
    }
    with pytest.raises(ValueError, match="duplicate issn_l"):
        build_database.validate_journals([valid, duplicate], "duplicates")
    with pytest.raises(ValueError, match="invalid jcr_quartile"):
        build_database.validate_journals([invalid], "invalid")


def test_atomic_json_write_replaces_complete_document(tmp_path):
    output = tmp_path / "journals.json"
    output.write_text('{"old": true}', encoding="utf-8")

    build_database.write_json_atomic(output, [{"issn_l": "1234-5678"}])

    assert json.loads(output.read_text(encoding="utf-8")) == [
        {"issn_l": "1234-5678"}
    ]
    assert not (tmp_path / ".journals.json.tmp").exists()


def test_atomic_json_write_preserves_old_document_on_failure(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "journals.json"
    output.write_text('{"old": true}', encoding="utf-8")

    def fail_dump(*args, **kwargs):
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(build_database.json, "dump", fail_dump)

    with pytest.raises(RuntimeError, match="serialization failed"):
        build_database.write_json_atomic(output, [{"new": True}])

    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}
    assert not (tmp_path / ".journals.json.tmp").exists()


def test_embedding_cache_prunes_noncanonical_entries_without_reencoding(
    tmp_path,
    monkeypatch,
):
    journal = {"issn_l": "1234-5678", "name": "Test Journal"}
    text_hash = hashlib.sha1(b"Test Journal").hexdigest()
    cache_path = tmp_path / "embeddings_cache.json"
    normalized_vector = [1.0] + [0.0] * 767
    cache_path.write_text(
        json.dumps(
            {
                "1234-5678": {"h": text_hash, "emb": normalized_vector},
                "9999-9999": {"h": "stale", "emb": normalized_vector},
            }
        ),
        encoding="utf-8",
    )

    class UnexpectedModel:
        def __init__(self, *args, **kwargs):
            raise AssertionError("cache hit should not load the model")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=UnexpectedModel),
    )
    monkeypatch.setattr(build_embeddings, "DATA_DIR", tmp_path)

    valid, matrix = build_embeddings.build_embeddings([journal], {}, batch_size=1)

    assert valid == [journal]
    assert matrix.shape == (1, 768)
    saved_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(saved_cache) == {"1234-5678"}
    assert saved_cache["1234-5678"]["v"] == build_embeddings.CACHE_SCHEMA_VERSION
    assert saved_cache["1234-5678"]["model"] == build_embeddings.MODEL_ID


def test_semantic_manifest_rejects_same_size_map_tampering(tmp_path, monkeypatch):
    journals = [
        {"issn_l": "1111-1111"},
        {"issn_l": "2222-2222"},
    ]
    matrix = np.zeros((2, 768), dtype="float32")
    matrix[0, 0] = 1.0
    matrix[1, 1] = 1.0

    monkeypatch.setattr(build_embeddings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(semantic_search, "DATA_DIR", tmp_path)
    index = build_embeddings.build_faiss_index(matrix)
    build_embeddings.save_artifacts(journals, matrix, index)

    loaded_index, index_map = semantic_search.load_index()
    assert loaded_index.ntotal == 2
    assert index_map == {0: "1111-1111", 1: "2222-2222"}

    map_path = tmp_path / "journal_index_map.json"
    map_path.write_text(
        json.dumps({"0": "2222-2222", "1": "1111-1111"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="generation"):
        semantic_search.load_index()
