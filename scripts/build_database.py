"""
build_database.py - 合并所有数据源生成最终期刊数据库

合并：
1. data/raw/sources_ssci_all.json (canonical OpenAlex 基础数据)
   可用 --include-legacy 显式补充旧 economics/demography topic 库
2. data/raw/computed_metrics.json (国人占比 + 年发文量)
3. data/raw/review_times.json (审稿时间线)
4. data/manual_supplement.json (JCR分区、中科院分区、APC等手动维护数据)

输出：
- data/journals_ssci.json
- data/journals_economics.json
- data/journals_demography.json
"""

import argparse
import json
import os
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
VALID_JCR_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}
VALID_CANONICAL_SOURCE_SCOPES = {"ssci_ahci", "scie_env_health"}
REVIEW_TYPE_PROVENANCE_FIELDS = {
    "review_type_source",
    "review_type_source_url",
    "review_type_last_checked",
    "review_type_verified",
}


def load_json(path):
    """Load JSON file, return empty list/dict on failure."""
    if not path.exists():
        print(f"  WARNING: {path} not found, skipping")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path, data):
    """Write JSON beside its destination, then atomically replace the output."""
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def normalize_jcr_quartile(value):
    """Normalize missing JCR sentinels without inventing a quartile."""
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"", "N/A", "NA", "-", "—"}:
        return None
    return normalized


def verified_review_type(manual):
    """Return review type only when the supplement records its provenance."""
    value = manual.get("review_type")
    if not value:
        return None
    if not any(manual.get(field) for field in REVIEW_TYPE_PROVENANCE_FIELDS):
        return None
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in {"single_blind", "double_blind"}:
        return None
    return normalized


def _tag_source(source, source_file, source_scope=None, force_scope=False):
    """Copy a raw source and attach explicit provenance for the final database."""
    tagged = dict(source)
    if force_scope:
        tagged["_source_scope"] = source_scope
    else:
        existing_scope = tagged.get("_source_scope")
        if existing_scope not in VALID_CANONICAL_SOURCE_SCOPES:
            raise ValueError(
                f"{source_file}: journal {tagged.get('issn_l') or tagged.get('name')!r} "
                f"has missing or invalid _source_scope {existing_scope!r}"
            )
    tagged["_source_file"] = source_file
    return tagged


def load_sources(include_legacy=False):
    """Load canonical unified sources, optionally adding explicitly tagged legacy data."""
    sources = []
    seen_issn_l = set()
    unified_path = RAW_DIR / "sources_ssci_all.json"

    if unified_path.exists():
        unified = load_json(unified_path)
        if not isinstance(unified, list):
            raise ValueError(f"{unified_path} must contain a JSON list")
        print(f"  SSCI unified sources: {len(unified)}")
        duplicate_issns = []
        missing_issn_count = 0
        for source in unified:
            issn_l = source.get("issn_l")
            if not issn_l:
                missing_issn_count += 1
                continue
            if issn_l in seen_issn_l:
                duplicate_issns.append(issn_l)
                continue
            seen_issn_l.add(issn_l)
            sources.append(
                _tag_source(
                    source,
                    source_file=unified_path.name,
                )
            )
        if duplicate_issns:
            print(
                f"  WARNING: skipped {len(duplicate_issns)} duplicate unified ISSN-L "
                f"records: {', '.join(duplicate_issns[:5])}"
            )
        if missing_issn_count:
            print(
                f"  WARNING: skipped {missing_issn_count} unified records "
                "without ISSN-L"
            )
    elif not include_legacy:
        raise FileNotFoundError(
            f"Canonical source file not found: {unified_path}. "
            "Run fetch_ssci_journals.py first, or pass --include-legacy "
            "explicitly for a diagnostic legacy-only build."
        )
    else:
        print("  WARNING: canonical source missing; building explicitly tagged legacy data only")

    if include_legacy:
        legacy_files = [
            ("sources_economics.json", "legacy_economics"),
            ("sources_demography.json", "legacy_demography"),
        ]
        for filename, source_scope in legacy_files:
            path = RAW_DIR / filename
            if not path.exists():
                continue
            legacy = load_json(path)
            if not isinstance(legacy, list):
                raise ValueError(f"{path} must contain a JSON list")
            added = 0
            for source in legacy:
                issn_l = source.get("issn_l")
                if not issn_l or issn_l in seen_issn_l:
                    continue
                seen_issn_l.add(issn_l)
                sources.append(
                    _tag_source(
                        source,
                        source_file=filename,
                        source_scope=source_scope,
                        force_scope=True,
                    )
                )
                added += 1
            print(f"  Legacy {filename}: +{added} explicitly tagged")

    return sources


def validate_journals(journals, label):
    """Fail before publishing malformed or duplicate journal records."""
    if not journals:
        raise ValueError(f"{label}: refusing to write an empty journal database")

    seen = set()
    errors = []
    for index, journal in enumerate(journals):
        issn_l = journal.get("issn_l")
        name = journal.get("name")
        source_scope = (journal.get("_meta") or {}).get("source_scope")
        quartile = journal.get("jcr_quartile")

        if not issn_l:
            errors.append(f"row {index}: missing issn_l")
        elif issn_l in seen:
            errors.append(f"row {index}: duplicate issn_l {issn_l}")
        else:
            seen.add(issn_l)
        if not name:
            errors.append(f"row {index}: missing name")
        if not source_scope:
            errors.append(f"row {index}: missing _meta.source_scope")
        if quartile is not None and quartile not in VALID_JCR_QUARTILES:
            errors.append(f"row {index}: invalid jcr_quartile {quartile!r}")

        if len(errors) >= 10:
            break

    if errors:
        raise ValueError(f"{label} validation failed: " + "; ".join(errors))


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
    scope_keywords = sorted({
        t["name"].strip().lower()
        for t in topics[:10]
        if isinstance(t, dict) and t.get("name")
    })

    journal = {
        # Identity
        "issn_l": issn_l,
        "name": source.get("name"),
        "abbreviation": get_abbreviation(source),
        "publisher": source.get("publisher"),
        "country_code": source.get("country_code"),
        "openalex_id": source.get("openalex_id"),
        "homepage_url": source.get("homepage_url"),

        # Topics (for matching)
        "topics": [
            {"name": t["name"], "score": t.get("count", 0), "subfield": t.get("subfield", "")}
            for t in topics[:15]
            if isinstance(t, dict) and t.get("name")
        ],
        "scope_keywords": scope_keywords,

        # Impact metrics
        "jcr_quartile": normalize_jcr_quartile(manual.get("jcr_quartile")),
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

        # Review timeline — Crossref (per-article evidence) is primary;
        # manual publisher-page numbers are a fallback for journals Crossref doesn't cover (e.g. Elsevier).
        "review_median_days": review.get("review_median_days")
        if review.get("review_median_days") is not None
        else manual.get("review_median_days"),
        "review_samples": review.get("samples_with_review", 0),
        "review_coverage": review.get("review_coverage", 0),
        "accept_to_online_days": review.get("accept_to_online_days")
        if review.get("accept_to_online_days") is not None
        else manual.get("accept_to_online_days"),
        "review_source": "crossref"
        if review.get("review_median_days") is not None
        else ("publisher_page" if manual.get("review_median_days") is not None else None),

        # Submission requirements
        "word_limit_min": manual.get("word_limit_min"),
        "word_limit_max": manual.get("word_limit_max"),
        "review_type": verified_review_type(manual),

        # Warnings and notes
        "warning_tags": manual.get("warning_tags", []),
        "notes": manual.get("notes", ""),

        # Metadata
        "_meta": {
            "last_api_update": date.today().isoformat(),
            "last_manual_update": manual.get("last_verified"),
            "has_manual_data": bool(manual),
            "source_scope": source.get("_source_scope"),
            "source_file": source.get("_source_file"),
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
    parser = argparse.ArgumentParser(description="Build merged journal databases")
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help=(
            "Add journals missing from sources_ssci_all.json using legacy economics/"
            "demography sources. Added records are explicitly tagged as legacy."
        ),
    )
    args = parser.parse_args()

    print("Loading data sources...")
    sources_all = load_sources(include_legacy=args.include_legacy)
    print(f"  Total unique sources: {len(sources_all)}")

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

    # Build unified database
    print("\nBuilding unified SSCI database...")
    all_journals = []
    for source in sources_all:
        journal = merge_journal(source, metrics_idx, review_idx, manual)
        if journal:
            all_journals.append(journal)

    # Sort by citedness (proxy for prestige)
    all_journals.sort(key=lambda j: j.get("citedness_2yr") or 0, reverse=True)
    validate_journals(all_journals, "journals_ssci")

    # Also maintain backward-compatible per-discipline files
    print("\nBuilding per-discipline databases (backward compat)...")
    source_by_issn = {source["issn_l"]: source for source in sources_all}

    econ_journals = [j for j in all_journals if is_relevant_economics(
        source_by_issn.get(j["issn_l"], {})
    )]
    validate_journals(econ_journals, "journals_economics")

    demo_journals = [j for j in all_journals if is_relevant_demography(
        source_by_issn.get(j["issn_l"], {})
    )]
    validate_journals(demo_journals, "journals_demography")

    # Validate every output before replacing any existing database.
    unified_output = DATA_DIR / "journals_ssci.json"
    econ_output = DATA_DIR / "journals_economics.json"
    demo_output = DATA_DIR / "journals_demography.json"
    write_json_atomic(unified_output, all_journals)
    print(f"  Saved {len(all_journals)} journals to {unified_output}")
    write_json_atomic(econ_output, econ_journals)
    print(f"  Economics: {len(econ_journals)} journals")
    write_json_atomic(demo_output, demo_journals)
    print(f"  Demography: {len(demo_journals)} journals")

    # Summary and quality check
    print("\n--- Quality Check ---")

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

    # Subject distribution
    from collections import Counter
    subjects = Counter()
    for j in all_journals:
        # Use JCR subject from supplement if available
        supp = manual.get(j["issn_l"], {})
        subj = supp.get("subject_category") or supp.get("subject_detail", "")
        if subj:
            # Extract primary subject
            primary = subj.split("(")[0].strip() if "(" in subj else subj
            subjects[primary] += 1
        else:
            subjects["(no subject)"] += 1

    print(f"\n  Subject distribution (top 15):")
    for subj, count in subjects.most_common(15):
        print(f"    {subj}: {count}")


if __name__ == "__main__":
    main()
