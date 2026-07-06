#!/usr/bin/env python3
"""
semantic_search.py - 基于 FAISS 向量索引的期刊语义搜索

对查询文本生成嵌入向量，在预建的 FAISS 索引中做近似最近邻搜索，
返回语义最相关的期刊列表。

输出 JSON 格式与 query_db.py 一致，结果中含 _semantic_score 字段。

Usage:
    python3 skill/scripts/semantic_search.py --query "labor market aging pension" --top 15
    python3 skill/scripts/semantic_search.py --query "fertility family policy" --top 20 --filter-quartile Q1
    python3 skill/scripts/semantic_search.py --query "migration integration" --top 15 --max-apc 3000
"""

import argparse
import json
import sys
from pathlib import Path

DATA_DIR = Path.home() / "journal-finder" / "data"


def load_index():
    """加载 FAISS 索引和索引映射文件。"""
    import faiss

    index_path = DATA_DIR / "journal_index.faiss"
    map_path = DATA_DIR / "journal_index_map.json"

    if not index_path.exists():
        print(
            json.dumps(
                {"error": "FAISS 索引不存在，请先运行 scripts/build_embeddings.py"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    index = faiss.read_index(str(index_path))

    with open(map_path, "r", encoding="utf-8") as f:
        # key 是字符串（JSON 规范），转为 int
        index_map = {int(k): v for k, v in json.load(f).items()}

    return index, index_map


def load_journals_map():
    """加载期刊元数据，返回 {issn_l: journal_dict}。"""
    path = DATA_DIR / "journals_ssci.json"
    with open(path, "r", encoding="utf-8") as f:
        journals = json.load(f)
    return {j["issn_l"]: j for j in journals}


def encode_query(query_text):
    """用 specter2 模型将查询文本编码为归一化向量。"""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("allenai/specter2_base")
    vec = model.encode([query_text], normalize_embeddings=True)
    return vec.astype("float32")


def semantic_search(query, index, index_map, top_k=50):
    """
    在 FAISS 索引中搜索与 query 最相似的期刊。

    返回 [(issn_l, score), ...] 列表，score 为余弦相似度（0~1）。
    top_k 取比 top 大的候选池，留出空间给后续过滤。
    """
    query_vec = encode_query(query)
    scores, indices = index.search(query_vec, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:  # FAISS 找不到结果时返回 -1
            continue
        issn = index_map.get(idx)
        if issn:
            results.append((issn, float(score)))

    return results


def apply_filters(candidates, journals_map, filter_quartile=None, max_apc=None):
    """对候选期刊做后过滤（分区、APC）。"""
    filtered = []
    q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}

    for issn, score in candidates:
        j = journals_map.get(issn)
        if not j:
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
            if apc is not None and apc > max_apc:
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
        help="只返回指定分区的期刊（精确匹配，如 Q1 只返回 Q1）",
    )
    parser.add_argument("--max-apc", type=int, default=None, help="APC 上限（USD）")
    args = parser.parse_args()

    # 加载索引和元数据
    index, index_map = load_index()
    journals_map = load_journals_map()

    # 搜索候选池：取 top * 5 个候选，留出过滤空间
    candidate_pool = max(args.top * 5, 100)
    candidates = semantic_search(args.query, index, index_map, top_k=candidate_pool)

    # 后过滤
    if args.filter_quartile or args.max_apc is not None:
        candidates = apply_filters(
            candidates, journals_map,
            filter_quartile=args.filter_quartile,
            max_apc=args.max_apc,
        )

    # 格式化输出
    results = format_results(candidates, journals_map, top_n=args.top)

    output = {
        "query": {
            "text": args.query,
            "filters": {
                "filter_quartile": args.filter_quartile,
                "max_apc": args.max_apc,
            },
        },
        "results_count": len(results),
        "results": results,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
