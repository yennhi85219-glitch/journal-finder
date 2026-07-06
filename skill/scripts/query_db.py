#!/usr/bin/env python3
"""
query_db.py - 期刊数据库查询脚本

从本地 JSON 数据库中筛选和排序期刊，返回 top N 候选。
供 Claude Code Skill 通过 Bash 调用。

匹配策略：
  - 关键词匹配（权重 0.4）+ 语义向量搜索（权重 0.6）混合排序
  - 若 FAISS 索引不存在，自动降级为纯关键词匹配

Usage:
    python3 query_db.py --discipline economics --keywords "labor,wage,employment"
    python3 query_db.py --discipline both --keywords "aging,pension,labor market" --sort speed
    python3 query_db.py --discipline demography --keywords "fertility,family" --oa-only --max-apc 3000
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

DB_DIR = Path.home() / "journal-finder" / "data"


def load_database(discipline):
    """Load journal database for specified discipline(s).

    Supports: 'all' (unified SSCI), 'economics', 'demography', 'both' (econ+demo legacy).
    """
    journals = []

    # Try unified SSCI database first for 'all'
    if discipline == "all":
        path = DB_DIR / "journals_ssci.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                journals = json.load(f)
            return journals
        # Fallback to loading all available files
        discipline = "both"

    if discipline in ("economics", "both"):
        path = DB_DIR / "journals_economics.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                journals.extend(json.load(f))

    if discipline in ("demography", "both"):
        path = DB_DIR / "journals_demography.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_issns = {j["issn_l"] for j in journals}
                for j in data:
                    if j["issn_l"] not in existing_issns:
                        journals.append(j)

    return journals


def compute_keyword_score(journal, keywords):
    """Score how well a journal matches the given keywords."""
    if not keywords:
        return 1.0

    score = 0.0
    keywords_lower = [k.strip().lower() for k in keywords]

    # Match against topic names
    for topic in journal.get("topics", []):
        topic_name = topic.get("name", "").lower()
        for kw in keywords_lower:
            if kw in topic_name:
                # Weight by topic position (earlier = more relevant)
                score += 2.0
            # Partial word match
            elif any(word in topic_name for word in kw.split()):
                score += 0.5

    # Match against scope keywords
    for scope_kw in journal.get("scope_keywords", []):
        for kw in keywords_lower:
            if kw in scope_kw or scope_kw in kw:
                score += 1.0

    # Match against journal name
    name_lower = journal.get("name", "").lower()
    for kw in keywords_lower:
        if kw in name_lower:
            score += 1.5

    # Normalize by number of keywords
    return score / len(keywords_lower)


def apply_filters(journals, args):
    """Apply hard filters to journal list."""
    filtered = journals

    if args.oa_only:
        filtered = [j for j in filtered if j.get("is_oa")]

    if args.max_apc is not None:
        filtered = [
            j for j in filtered
            if j.get("apc_usd") is None or j["apc_usd"] <= args.max_apc
        ]

    if args.min_quartile:
        q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        max_q = q_map.get(args.min_quartile, 4)
        filtered = [
            j for j in filtered
            if j.get("jcr_quartile") is None or q_map.get(j["jcr_quartile"], 4) <= max_q
        ]

    # Exclude non-submittable publications (working papers, reports, OECD surveys, etc.)
    exclude_patterns = [
        "working paper", "discussion note", "staff paper",
        "oecd economic surveys", "oecd journal", "oecd social",
        "oecd employment", "oecd pensions",
        "imf staff", "world bank", "dynamics",
        "outlook", "briefing",
    ]
    filtered = [
        j for j in filtered
        if not any(pat in j.get("name", "").lower() for pat in exclude_patterns)
    ]

    return filtered


def get_normalized_impact(journal):
    """Get a normalized impact score (0-10 scale) for sorting.
    Manual IF takes priority; falls back to citedness_2yr with a cap."""
    if journal.get("impact_factor"):
        return min(journal["impact_factor"], 20)  # Cap at 20
    citedness = journal.get("citedness_2yr") or 0
    # Cap citedness at 15 to prevent outliers from dominating
    return min(citedness, 15)


def sort_journals(journals, sort_mode, keywords):
    """Sort journals by the specified mode."""
    for j in journals:
        j["_keyword_score"] = compute_keyword_score(j, keywords)

    # Minimum keyword relevance threshold
    min_score = 0.5 if keywords else 0
    journals = [j for j in journals if j["_keyword_score"] >= min_score]

    if sort_mode == "speed":
        # Prefer journals with fast review (lower days = better)
        def key(j):
            review = j.get("review_median_days")
            if review is None:
                review = 9999
            # Keyword relevance as tiebreaker
            return (review, -j["_keyword_score"])
    elif sort_mode == "prestige":
        # Impact-weighted, keyword as secondary
        def key(j):
            impact = get_normalized_impact(j)
            return (-(impact * 0.6 + j["_keyword_score"] * 0.4))
    elif sort_mode == "cn_friendly":
        # Prefer high CN ratio among relevant journals
        def key(j):
            cn = j.get("cn_author_ratio") or 0
            return (-(j["_keyword_score"] * 0.4 + cn * 100 * 0.6))
    else:
        # Default: balanced - keyword relevance weighted more heavily
        def key(j):
            impact = get_normalized_impact(j)
            return (-(j["_keyword_score"] * 0.6 + impact * 0.4))

    journals.sort(key=key)
    return journals


def format_output(journals, top_n=15):
    """Format top N journals for output."""
    results = []
    for j in journals[:top_n]:
        # Only include journals with some keyword relevance
        if j.get("_keyword_score", 0) <= 0:
            continue

        record = {
            "name": j["name"],
            "abbreviation": j.get("abbreviation"),
            "issn_l": j["issn_l"],
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
            "topics": [t["name"] for t in j.get("topics", [])[:5]],
            "_keyword_score": round(j.get("_keyword_score", 0), 2),
        }
        results.append(record)

    return results


def run_semantic_search(query_text, top_k, filter_quartile=None, max_apc=None):
    """
    调用 semantic_search.py 获取语义搜索结果。

    返回 {issn_l: _semantic_score} 字典。
    若索引不存在或调用失败，返回空字典（自动降级为纯关键词模式）。
    """
    faiss_index = DATA_DIR / "journal_index.faiss"
    if not faiss_index.exists():
        return {}

    script_path = Path(__file__).parent / "semantic_search.py"
    cmd = [
        sys.executable, str(script_path),
        "--query", query_text,
        "--top", str(top_k),
    ]
    if filter_quartile:
        cmd += ["--filter-quartile", filter_quartile]
    if max_apc is not None:
        cmd += ["--max-apc", str(max_apc)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
        return {r["issn_l"]: r["_semantic_score"] for r in data.get("results", [])}
    except Exception:
        return {}


def merge_results(keyword_journals, semantic_scores, top_n):
    """
    合并关键词匹配结果和语义搜索结果。

    策略：
      - 关键词分数归一化到 [0, 1]，权重 0.4
      - 语义分数已在 [0, 1]（余弦相似度），权重 0.6
      - 对两个来源的 issn_l 取并集，按综合分排序
      - 返回 top_n 条结果
    """
    # 归一化关键词分数
    kw_scores = {j["issn_l"]: j.get("_keyword_score", 0) for j in keyword_journals}
    max_kw = max(kw_scores.values(), default=1) or 1
    kw_normalized = {issn: s / max_kw for issn, s in kw_scores.items()}

    # 收集所有候选期刊的 issn_l（两个来源的并集）
    all_issns = set(kw_normalized.keys()) | set(semantic_scores.keys())

    # 构建综合分数字典
    combined = {}
    for issn in all_issns:
        kw = kw_normalized.get(issn, 0.0)
        sem = semantic_scores.get(issn, 0.0)

        if semantic_scores:
            # 两个来源都有数据时，加权合并
            combined[issn] = kw * 0.4 + sem * 0.6
        else:
            # 语义搜索不可用时，降级为纯关键词分
            combined[issn] = kw

    # 按综合分降序排列
    sorted_issns = sorted(combined.items(), key=lambda x: -x[1])[:top_n]

    # 重建期刊记录列表（优先从关键词结果取，补充 _semantic_score）
    journal_map = {j["issn_l"]: j for j in keyword_journals}
    results = []
    for issn, score in sorted_issns:
        if issn in journal_map:
            j = journal_map[issn].copy()
        else:
            # 仅语义搜索命中、关键词未命中的期刊：从数据库加载基础信息
            journals_all = load_database("all")
            j_match = next((x for x in journals_all if x["issn_l"] == issn), None)
            if not j_match:
                continue
            j = {
                "name": j_match["name"],
                "abbreviation": j_match.get("abbreviation"),
                "issn_l": issn,
                "publisher": j_match.get("publisher"),
                "jcr_quartile": j_match.get("jcr_quartile"),
                "cas_zone": j_match.get("cas_zone"),
                "impact_factor": j_match.get("impact_factor"),
                "citedness_2yr": j_match.get("citedness_2yr"),
                "is_oa": j_match.get("is_oa"),
                "oa_type": j_match.get("oa_type"),
                "apc_usd": j_match.get("apc_usd"),
                "apc_waiver": j_match.get("apc_waiver"),
                "cn_author_ratio": j_match.get("cn_author_ratio"),
                "annual_volume_2024": j_match.get("annual_volume_2024"),
                "review_median_days": j_match.get("review_median_days"),
                "accept_to_online_days": j_match.get("accept_to_online_days"),
                "review_coverage": j_match.get("review_coverage"),
                "word_limit_max": j_match.get("word_limit_max"),
                "review_type": j_match.get("review_type"),
                "warning_tags": j_match.get("warning_tags", []),
                "notes": j_match.get("notes", ""),
                "topics": [t.get("name", t) if isinstance(t, dict) else t
                           for t in j_match.get("topics", [])[:5]],
                "_keyword_score": 0.0,
            }

        j["_semantic_score"] = round(semantic_scores.get(issn, 0.0), 4)
        j["_combined_score"] = round(score, 4)
        results.append(j)

    return results


def main():
    parser = argparse.ArgumentParser(description="Query journal database")
    parser.add_argument(
        "--discipline", choices=["economics", "demography", "both", "all"],
        default="all", help="Which discipline database to search (all = full SSCI/AHCI)"
    )
    parser.add_argument(
        "--keywords", type=str, default="",
        help="Comma-separated keywords to match (e.g., 'labor,wage,employment')"
    )
    parser.add_argument(
        "--max-apc", type=int, default=None,
        help="Maximum APC in USD"
    )
    parser.add_argument(
        "--oa-only", action="store_true",
        help="Only show OA journals"
    )
    parser.add_argument(
        "--min-quartile", type=str, default=None,
        choices=["Q1", "Q2", "Q3", "Q4"],
        help="Minimum JCR quartile (e.g., Q2 means Q1 and Q2)"
    )
    parser.add_argument(
        "--sort", type=str, default="balanced",
        choices=["speed", "prestige", "cn_friendly", "balanced"],
        help="Sorting strategy"
    )
    parser.add_argument(
        "--top", type=int, default=15,
        help="Number of results to return"
    )

    args = parser.parse_args()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    # --- 关键词匹配部分 ---
    journals = load_database(args.discipline)
    if not journals:
        print(json.dumps({"error": "No journals found. Run build_database.py first."}))
        sys.exit(1)

    journals = apply_filters(journals, args)
    # 扩大候选池再交给合并逻辑，避免语义命中的期刊被提前截断
    candidate_pool = max(args.top * 5, 100)
    kw_journals = sort_journals(journals, args.sort, keywords)
    kw_results_raw = format_output(kw_journals, candidate_pool)

    # --- 语义搜索部分 ---
    query_text = " ".join(keywords) if keywords else ""
    semantic_scores = {}
    if query_text:
        semantic_scores = run_semantic_search(
            query_text,
            top_k=candidate_pool,
            filter_quartile=args.min_quartile,
            max_apc=args.max_apc,
        )

    # --- 合并 ---
    if semantic_scores:
        # 有语义结果：加权合并
        results = merge_results(kw_results_raw, semantic_scores, top_n=args.top)
    else:
        # 无语义结果（索引未建或无关键词）：纯关键词
        results = format_output(kw_journals, args.top)

    output = {
        "query": {
            "discipline": args.discipline,
            "keywords": keywords,
            "sort": args.sort,
            "semantic_search": bool(semantic_scores),
            "filters": {
                "oa_only": args.oa_only,
                "max_apc": args.max_apc,
                "min_quartile": args.min_quartile,
            },
        },
        "total_in_database": len(load_database(args.discipline)),
        "results_count": len(results),
        "results": results,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
