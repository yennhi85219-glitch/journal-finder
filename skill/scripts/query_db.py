#!/usr/bin/env python3
"""
query_db.py - 期刊数据库查询脚本

从本地 JSON 数据库中筛选和排序期刊，返回 top N 候选。
供 Claude Code Skill 通过 Bash 调用。

Usage:
    python3 query_db.py --discipline economics --keywords "labor,wage,employment"
    python3 query_db.py --discipline both --keywords "aging,pension,labor market" --sort speed
    python3 query_db.py --discipline demography --keywords "fertility,family" --oa-only --max-apc 3000
"""

import argparse
import json
import sys
from pathlib import Path

DB_DIR = Path.home() / "journal-finder" / "data"


def load_database(discipline):
    """Load journal database for specified discipline(s)."""
    journals = []

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
                # Avoid duplicates when loading both
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


def main():
    parser = argparse.ArgumentParser(description="Query journal database")
    parser.add_argument(
        "--discipline", choices=["economics", "demography", "both"],
        default="both", help="Which discipline database to search"
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

    # Load and process
    journals = load_database(args.discipline)
    if not journals:
        print(json.dumps({"error": "No journals found. Run build_database.py first."}))
        sys.exit(1)

    # Filter
    journals = apply_filters(journals, args)

    # Sort
    journals = sort_journals(journals, args.sort, keywords)

    # Output
    results = format_output(journals, args.top)

    output = {
        "query": {
            "discipline": args.discipline,
            "keywords": keywords,
            "sort": args.sort,
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
