"""
build_database.py - 合并所有数据源生成最终期刊数据库

合并：
1. data/raw/sources_economics.json, sources_demography.json (OpenAlex 基础数据)
2. data/raw/computed_metrics.json (国人占比 + 年发文量)
3. data/raw/review_times.json (审稿时间线)
4. data/manual_supplement.json (JCR分区、中科院分区、APC等手动维护数据)

输出：
- data/journals_economics.json
- data/journals_demography.json
"""

import json
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"


def load_json(path):
    """Load JSON file, return empty list/dict on failure."""
    if not path.exists():
        print(f"  WARNING: {path} not found, skipping")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_metrics_index(metrics_list):
    """Index computed metrics by issn_l."""
    return {m["issn_l"]: m for m in metrics_list if m.get("issn_l")}


def build_review_index(review_list):
    """Index review times by issn_l."""
    return {r["issn_l"]: r for r in review_list if r.get("issn_l")}


def determine_oa_type(source, manual):
    """Determine OA type from available data."""
    if manual and manual.get("oa_type"):
        return manual["oa_type"]
    if source.get("is_oa"):
        if source.get("is_in_doaj"):
            return "gold"
        return "hybrid"
    return "subscription"


def get_abbreviation(source):
    """Extract abbreviation from alternate_titles."""
    alt = source.get("alternate_titles", [])
    # Prefer short abbreviations (< 10 chars)
    for title in alt:
        if len(title) <= 10:
            return title
    if alt:
        return alt[0]
    return None


def merge_journal(source, metrics_idx, review_idx, manual_supplement):
    """Merge all data sources for a single journal into final schema."""
    issn_l = source.get("issn_l")
    if not issn_l:
        return None

    metrics = metrics_idx.get(issn_l, {})
    review = review_idx.get(issn_l, {})
    manual = manual_supplement.get(issn_l, {})

    # Extract scope keywords from topics
    topics = source.get("topics", [])
    scope_keywords = list({
        t["name"].lower() for t in topics[:10]
    })

    journal = {
        # Identity
        "issn_l": issn_l,
        "name": source["name"],
        "abbreviation": get_abbreviation(source),
        "publisher": source.get("publisher"),
        "country_code": source.get("country_code"),
        "openalex_id": source.get("openalex_id"),
        "homepage_url": source.get("homepage_url"),

        # Topics (for matching)
        "topics": [
            {"name": t["name"], "score": t.get("count", 0), "subfield": t.get("subfield", "")}
            for t in topics[:15]
        ],
        "scope_keywords": scope_keywords,

        # Impact metrics
        "jcr_quartile": manual.get("jcr_quartile"),
        "cas_zone": manual.get("cas_zone"),
        "impact_factor": manual.get("impact_factor"),
        "citedness_2yr": source.get("citedness_2yr", 0),
        "h_index": source.get("h_index", 0),

        # OA and costs
        "is_oa": source.get("is_oa", False),
        "oa_type": determine_oa_type(source, manual),
        "apc_usd": manual.get("apc_usd") if manual.get("apc_usd") is not None else source.get("apc_usd"),
        "apc_waiver": manual.get("apc_waiver"),

        # Volume and audience
        "cn_author_ratio": metrics.get("cn_author_ratio"),
        "annual_volume_2024": metrics.get("annual_volume_2024"),
        "annual_volume_2023": metrics.get("annual_volume_2023"),

        # Review timeline
        "review_median_days": review.get("review_median_days"),
        "review_samples": review.get("samples_with_review", 0),
        "review_coverage": review.get("review_coverage", 0),
        "accept_to_online_days": review.get("accept_to_online_days"),

        # Submission requirements
        "word_limit_min": manual.get("word_limit_min"),
        "word_limit_max": manual.get("word_limit_max"),
        "review_type": manual.get("review_type"),

        # Warnings and notes
        "warning_tags": manual.get("warning_tags", []),
        "notes": manual.get("notes", ""),

        # Metadata
        "_meta": {
            "last_api_update": date.today().isoformat(),
            "last_manual_update": manual.get("last_verified"),
            "has_manual_data": bool(manual),
        },
    }

    return journal


def is_relevant_economics(journal_source):
    """Check if a journal is primarily economics-related based on topics."""
    topics = journal_source.get("topics", [])
    if not topics:
        return True  # Include if no topics (can't filter)

    # Check if any of the top 5 topics are in economics-related fields
    econ_fields = {
        "Economics, Econometrics and Finance",
        "Business, Management and Accounting",
    }
    econ_subfields = {
        "Economics and Econometrics",
        "Finance",
        "General Economics, Econometrics and Finance",
        "Accounting",
    }

    for t in topics[:5]:
        if t.get("field") in econ_fields or t.get("subfield") in econ_subfields:
            return True
    return False


def is_relevant_demography(journal_source):
    """Check if a journal is primarily demography/population-related."""
    topics = journal_source.get("topics", [])
    if not topics:
        return True

    demo_keywords = {
        "demography", "population", "family", "fertility", "mortality",
        "migration", "aging", "demographic", "gender",
    }

    # Check topic names for demography keywords
    for t in topics[:5]:
        name_lower = t.get("name", "").lower()
        if any(kw in name_lower for kw in demo_keywords):
            return True

    # Also check subfield
    demo_subfields = {"Demography", "Gender Studies"}
    for t in topics[:5]:
        if t.get("subfield") in demo_subfields:
            return True

    return False


def main():
    print("Loading data sources...")

    # Load raw sources
    sources_econ = load_json(RAW_DIR / "sources_economics.json")
    sources_demo = load_json(RAW_DIR / "sources_demography.json")
    print(f"  Economics sources: {len(sources_econ)}")
    print(f"  Demography sources: {len(sources_demo)}")

    # Load computed metrics
    metrics_raw = load_json(RAW_DIR / "computed_metrics.json")
    metrics_idx = build_metrics_index(metrics_raw)
    print(f"  Metrics entries: {len(metrics_idx)}")

    # Load review times
    review_raw = load_json(RAW_DIR / "review_times.json")
    review_idx = build_review_index(review_raw)
    print(f"  Review time entries: {len(review_idx)}")

    # Load manual supplement
    manual = load_json(DATA_DIR / "manual_supplement.json")
    if isinstance(manual, list):
        manual = {}
    print(f"  Manual supplement entries: {len(manual)}")

    # Filter and merge Economics journals
    print("\nBuilding Economics database...")
    econ_filtered = [s for s in sources_econ if is_relevant_economics(s)]
    print(f"  After relevance filter: {len(econ_filtered)} (from {len(sources_econ)})")

    econ_journals = []
    for source in econ_filtered:
        journal = merge_journal(source, metrics_idx, review_idx, manual)
        if journal:
            econ_journals.append(journal)

    # Sort by citedness (proxy for prestige)
    econ_journals.sort(key=lambda j: j.get("citedness_2yr") or 0, reverse=True)

    econ_output = DATA_DIR / "journals_economics.json"
    with open(econ_output, "w", encoding="utf-8") as f:
        json.dump(econ_journals, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(econ_journals)} journals to {econ_output}")

    # Filter and merge Demography journals
    print("\nBuilding Demography database...")
    demo_filtered = [s for s in sources_demo if is_relevant_demography(s)]
    print(f"  After relevance filter: {len(demo_filtered)} (from {len(sources_demo)})")

    demo_journals = []
    seen_issn = set()  # Avoid duplicates with economics
    for source in demo_filtered:
        issn_l = source.get("issn_l")
        if issn_l in seen_issn:
            continue
        seen_issn.add(issn_l)
        journal = merge_journal(source, metrics_idx, review_idx, manual)
        if journal:
            demo_journals.append(journal)

    demo_journals.sort(key=lambda j: j.get("citedness_2yr") or 0, reverse=True)

    demo_output = DATA_DIR / "journals_demography.json"
    with open(demo_output, "w", encoding="utf-8") as f:
        json.dump(demo_journals, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(demo_journals)} journals to {demo_output}")

    # Summary and quality check
    print("\n--- Quality Check ---")

    # Check known journals
    all_journals = econ_journals + demo_journals
    all_issns = {j["issn_l"] for j in all_journals}

    known_must_have = {
        "0002-8282": "American Economic Review",
        "0033-5533": "The Quarterly Journal of Economics",
        "0012-9682": "Econometrica",
        "0070-3370": "Demography",
        "0098-7921": "Population and Development Review",
    }

    for issn, name in known_must_have.items():
        status = "FOUND" if issn in all_issns else "MISSING"
        print(f"  [{status}] {name}")

    # Data completeness stats
    has_metrics = sum(1 for j in all_journals if j.get("cn_author_ratio") is not None)
    has_review = sum(1 for j in all_journals if j.get("review_median_days") is not None)
    has_manual = sum(1 for j in all_journals if j.get("_meta", {}).get("has_manual_data"))

    total = len(all_journals)
    print(f"\n  Total journals: {total}")
    print(f"  With metrics data: {has_metrics} ({has_metrics/total*100:.1f}%)")
    print(f"  With review time data: {has_review} ({has_review/total*100:.1f}%)")
    print(f"  With manual supplement: {has_manual} ({has_manual/total*100:.1f}%)")


if __name__ == "__main__":
    main()
