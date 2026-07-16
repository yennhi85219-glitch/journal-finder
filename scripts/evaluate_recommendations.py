#!/usr/bin/env python3
"""Run the optional real-data Top-K recommendation quality benchmark."""

import argparse
import copy
import hashlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "recommendation_quality_cases.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


query_db = load_module(
    "benchmark_query_db",
    ROOT / "skill" / "scripts" / "query_db.py",
)
semantic_search = load_module(
    "benchmark_semantic_search",
    ROOT / "skill" / "scripts" / "semantic_search.py",
)
semantic_search.DATA_DIR = DATA_DIR


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_case(case, journals, index, index_map, query_vector):
    options = Namespace(
        oa_only=False,
        max_apc=None,
        min_quartile=None,
        max_review_days=None,
        require_review_data=False,
        include_review_only=False,
        sort="balanced",
        priorities="fit",
    )
    working_journals = [journal.copy() for journal in journals]
    keyword_candidates = query_db.sort_journals(
        working_journals,
        options.sort,
        case["keywords"],
    )

    scores, positions = index.search(query_vector[None, :], index.ntotal)
    semantic_scores = {
        index_map[int(position)]: float(score)
        for score, position in zip(
            scores[0][: query_db.RECALL_POOL_SIZE],
            positions[0][: query_db.RECALL_POOL_SIZE],
        )
        if position >= 0
    }
    merged = query_db.merge_results(
        query_db.format_output(
            keyword_candidates,
            query_db.RECALL_POOL_SIZE,
        ),
        semantic_scores,
        top_n=query_db.RECALL_POOL_SIZE,
        all_journals=working_journals,
    )
    ranked = query_db.rank_candidates(
        query_db.apply_filters(merged, options),
        options,
        top_n=15,
    )

    result_ids = [journal["issn_l"] for journal in ranked]
    positives = set(case["positive_issns"])
    failures = []
    for cutoff in (5, 10):
        minimum = case.get(f"min_positive_top{cutoff}")
        if minimum is not None:
            actual = len(set(result_ids[:cutoff]) & positives)
            if actual < minimum:
                failures.append(
                    f"positive@{cutoff}={actual}, expected >= {minimum}"
                )
        forbidden = set(case.get(f"forbidden_top{cutoff}", []))
        violations = sorted(set(result_ids[:cutoff]) & forbidden)
        if violations:
            failures.append(f"forbidden@{cutoff}={violations}")

    if (
        case.get("require_positive_top1")
        and (not result_ids or result_ids[0] not in positives)
    ):
        failures.append("top1 is not in the positive set")

    return ranked, failures


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate real-data Top-K journal recommendation quality"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()

    benchmark = json.loads(args.cases.read_text(encoding="utf-8"))
    db_path = DATA_DIR / "journals_ssci.json"
    journals = json.loads(db_path.read_text(encoding="utf-8"))
    index, index_map = semantic_search.load_index()

    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(
            semantic_search.MODEL_ID,
            local_files_only=True,
        )
    except Exception:
        model = SentenceTransformer(semantic_search.MODEL_ID)

    cases = benchmark["cases"]
    semantic_queries = [
        query_db.build_semantic_query(case["keywords"])
        for case in cases
    ]
    query_vectors = model.encode(
        semantic_queries,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype("float32")

    failed = 0
    print(
        f"DB={len(journals)} sha256={file_sha256(db_path)[:12]} "
        f"cases={len(cases)}"
    )
    for case, query_vector in zip(cases, query_vectors):
        ranked, failures = evaluate_case(
            case,
            journals,
            index,
            index_map,
            query_vector,
        )
        status = "PASS" if not failures else "FAIL"
        failed += bool(failures)
        top_names = " | ".join(journal["name"] for journal in ranked[:3])
        print(f"{status} {case['id']}: {top_names}")
        for failure in failures:
            print(f"  - {failure}")

    print(f"Summary: {len(cases) - failed}/{len(cases)} cases passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
