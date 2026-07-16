#!/usr/bin/env python3
"""
semantic_search.py - 基于 FAISS 向量索引的期刊语义搜索

对查询文本生成嵌入向量，在预建的 FAISS 索引中做近似最近邻搜索，
返回语义最相关的期刊列表。

输出 JSON 格式与 query_db.py 一致，结果中含 _semantic_score 字段。

Usage:
    python skill/scripts/semantic_search.py --query "labor market aging pension" --top 15
    python skill/scripts/semantic_search.py --query "fertility family policy" --top 20 --filter-quartile Q1
    python skill/scripts/semantic_search.py --query "migration integration" --top 15 --max-apc 3000
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import DataDirectoryError, resolve_data_dir


DATA_DIR = None
MODEL_ID = "allenai/specter2_base"
EXPECTED_EMBEDDING_DIM = 768


def set_data_dir(path=None):
    global DATA_DIR
    DATA_DIR = resolve_data_dir(__file__, explicit=path)
    return DATA_DIR


def get_data_dir():
    return DATA_DIR or set_data_dir()


def file_sha256(path):
    """Return a streaming SHA256 checksum for an artifact file."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_index():
    """加载 FAISS 索引和索引映射文件。"""
    import faiss

    data_dir = get_data_dir()
    index_path = data_dir / "journal_index.faiss"
    map_path = data_dir / "journal_index_map.json"
    meta_path = data_dir / "journal_index_meta.json"

    if not all(path.exists() for path in (index_path, map_path, meta_path)):
        print(
            json.dumps(
                {"error": "FAISS 语义资产不完整，请先运行 scripts/build_embeddings.py"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    with open(meta_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    if (
        manifest.get("version") != 1
        or manifest.get("model_id") != MODEL_ID
        or manifest.get("embedding_dim") != EXPECTED_EMBEDDING_DIM
        or manifest.get("index_sha256") != file_sha256(index_path)
        or manifest.get("map_sha256") != file_sha256(map_path)
    ):
        raise RuntimeError(
            "FAISS 索引 generation 校验失败；请重新运行 "
            "python scripts/build_embeddings.py"
        )

    index = faiss.read_index(str(index_path))

    with open(map_path, "r", encoding="utf-8") as f:
        # key 是字符串（JSON 规范），转为 int
        index_map = {int(k): v for k, v in json.load(f).items()}

    expected_positions = set(range(len(index_map)))
    if (
        index.ntotal != len(index_map)
        or index.d != EXPECTED_EMBEDDING_DIM
        or manifest.get("journal_count") != len(index_map)
        or set(index_map) != expected_positions
        or len(set(index_map.values())) != len(index_map)
        or manifest.get("ordered_issn_sha256")
        != hashlib.sha256(
            "\n".join(index_map[position] for position in range(len(index_map))).encode(
                "utf-8"
            )
        ).hexdigest()
    ):
        raise RuntimeError(
            "FAISS 索引与位置映射结构不一致；请重新运行 "
            "python scripts/build_embeddings.py"
        )

    return index, index_map


def load_journals_map():
    """加载期刊元数据，返回 {issn_l: journal_dict}。"""
    path = get_data_dir() / "journals_ssci.json"
    with open(path, "r", encoding="utf-8") as f:
        journals = json.load(f)
    return {j["issn_l"]: j for j in journals}


def encode_query(query_text):
    """用 specter2 模型将查询文本编码为归一化向量。"""
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(MODEL_ID, local_files_only=True)
    except Exception:
        model = SentenceTransformer(MODEL_ID)
    vec = model.encode([query_text], normalize_embeddings=True)
    return vec.astype("float32")


def semantic_search_with_vector(query_vec, index, index_map, top_k=50):
    """
    在 FAISS 索引中搜索与 query 最相似的期刊。

    返回 [(issn_l, score), ...] 列表，score 为余弦相似度（0~1）。
    top_k 取比 top 大的候选池，留出空间给后续过滤。
    """
    scores, indices = index.search(query_vec, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:  # FAISS 找不到结果时返回 -1
            continue
        issn = index_map.get(idx)
        if issn:
            results.append((issn, float(score)))

    return results


def apply_filters(
    candidates,
    journals_map,
    filter_quartile=None,
    max_apc=None,
    oa_only=False,
    max_review_days=None,
    require_review_data=False,
):
    """对候选期刊做与 query_db 一致的硬过滤。"""
    filtered = []
    q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}

    for issn, score in candidates:
        j = journals_map.get(issn)
        if not j:
            continue

        if oa_only and not j.get("is_oa"):
            continue

        # 分区过滤：--filter-quartile Q1 表示只要 Q1
        if filter_quartile:
            jq = j.get("jcr_quartile")
            if jq is None:
                continue  # 无分区信息则排除
            if q_map.get(jq, 9) > q_map.get(filter_quartile, 4):
                continue

        # APC 上限过滤
        if max_apc is not None:
            apc = j.get("apc_usd")
            if apc is None or apc > max_apc:
                continue

        review_days = j.get("review_median_days")
        if max_review_days is not None:
            if review_days is not None and review_days > max_review_days:
                continue
        if require_review_data and review_days is None:
            continue

        filtered.append((issn, score))

    return filtered


def format_results(candidates, journals_map, top_n):
    """将候选列表格式化为输出 JSON（与 query_db.py 输出字段一致）。"""
    results = []
    for issn, score in candidates[:top_n]:
        j = journals_map.get(issn, {})

        # topics 字段兼容字典和字符串两种格式
        topics_raw = j.get("topics", [])
        topic_names = []
        for t in topics_raw[:5]:
            if isinstance(t, dict):
                topic_names.append(t.get("name", ""))
            elif isinstance(t, str):
                topic_names.append(t)

        record = {
            "name": j.get("name", ""),
            "abbreviation": j.get("abbreviation"),
            "issn_l": issn,
            "publisher": j.get("publisher"),
            "jcr_quartile": j.get("jcr_quartile"),
            "cas_zone": j.get("cas_zone"),
            "impact_factor": j.get("impact_factor"),
            "citedness_2yr": j.get("citedness_2yr"),
            "is_oa": j.get("is_oa"),
            "oa_type": j.get("oa_type"),
            "apc_usd": j.get("apc_usd"),
            "apc_waiver": j.get("apc_waiver"),
            "cn_author_ratio": j.get("cn_author_ratio"),
            "annual_volume_2024": j.get("annual_volume_2024"),
            "review_median_days": j.get("review_median_days"),
            "review_samples": j.get("review_samples"),
            "accept_to_online_days": j.get("accept_to_online_days"),
            "review_coverage": j.get("review_coverage"),
            "word_limit_max": j.get("word_limit_max"),
            "review_type": j.get("review_type"),
            "warning_tags": j.get("warning_tags", []),
            "notes": j.get("notes", ""),
            "topics": [n for n in topic_names if n],
            "_semantic_score": round(score, 4),
        }
        results.append(record)

    return results


def main():
    parser = argparse.ArgumentParser(description="期刊语义搜索")
    parser.add_argument("--query", type=str, required=True, help="查询文本")
    parser.add_argument("--top", type=int, default=15, help="返回结果数量（默认 15）")
    parser.add_argument(
        "--filter-quartile",
        type=str,
        default=None,
        choices=["Q1", "Q2", "Q3", "Q4"],
        help="最低 JCR 分区（如 Q2 返回 Q1/Q2）",
    )
    parser.add_argument("--max-apc", type=int, default=None, help="APC 上限（USD）")
    parser.add_argument("--oa-only", action="store_true", help="只返回 OA 期刊")
    parser.add_argument(
        "--max-review-days",
        type=int,
        default=None,
        help="最长审稿中位数（缺失值保留，除非同时要求审稿数据）",
    )
    parser.add_argument(
        "--require-review-data",
        action="store_true",
        help="只返回有审稿时间数据的期刊",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing journals_ssci.json and semantic assets",
    )
    args = parser.parse_args()

    try:
        set_data_dir(args.data_dir)
    except DataDirectoryError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)

    # 先编码，再加载 FAISS。这个顺序在部分 macOS/conda 环境下更稳定。
    query_vec = encode_query(args.query)

    # 加载索引和元数据
    index, index_map = load_index()
    journals_map = load_journals_map()
    if set(index_map.values()) != set(journals_map):
        raise RuntimeError(
            "FAISS 位置映射与 canonical 数据库期刊集合不一致；请重新运行 "
            "python scripts/build_embeddings.py"
        )
    if query_vec.shape != (1, index.d):
        raise RuntimeError(
            f"Query embedding shape {query_vec.shape} does not match FAISS dimension "
            f"{index.d}"
        )

    # IndexFlatIP already scans the full corpus. Returning all positions lets
    # hard filters run before truncation, so restrictive filters keep recall.
    candidate_pool = index.ntotal
    candidates = semantic_search_with_vector(query_vec, index, index_map, top_k=candidate_pool)

    # 后过滤
    if (
        args.filter_quartile
        or args.max_apc is not None
        or args.oa_only
        or args.max_review_days is not None
        or args.require_review_data
    ):
        candidates = apply_filters(
            candidates, journals_map,
            filter_quartile=args.filter_quartile,
            max_apc=args.max_apc,
            oa_only=args.oa_only,
            max_review_days=args.max_review_days,
            require_review_data=args.require_review_data,
        )

    # 格式化输出
    results = format_results(candidates, journals_map, top_n=args.top)

    output = {
        "query": {
            "text": args.query,
            "filters": {
                "filter_quartile": args.filter_quartile,
                "max_apc": args.max_apc,
                "oa_only": args.oa_only,
                "max_review_days": args.max_review_days,
                "require_review_data": args.require_review_data,
            },
        },
        "results_count": len(results),
        "results": results,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
