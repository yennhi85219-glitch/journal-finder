"""
fetch_metrics.py - 计算每个期刊的国人占比和年发文量

对每个期刊查询 OpenAlex Works API：
1. 年发文量：filter by source ID + publication_year
2. 国人占比：filter by source ID + publication_year + authorships.institutions.country_code:CN

输出：data/raw/computed_metrics.json (keyed by issn_l)
"""

import json
import time
import requests
from pathlib import Path
from tqdm import tqdm

BASE_URL = "https://api.openalex.org"
MAILTO = "test@example.com"

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_FILE = RAW_DIR / "computed_metrics.json"

# Years to compute metrics for
YEARS = [2023, 2024, 2025]


def create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": f"JournalFinder/1.0 (mailto:{MAILTO})",
    })
    return session


def safe_get(session, url, retries=3):
    """GET with retry logic."""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code >= 500:
                time.sleep(2)
                continue
        except requests.RequestException:
            time.sleep(2)
    return None


def get_works_count(session, source_id, year, country_code=None):
    """Get the count of works for a source in a given year, optionally filtered by country."""
    filters = [
        f"primary_location.source.id:{source_id}",
        f"publication_year:{year}",
    ]
    if country_code:
        filters.append(f"authorships.institutions.country_code:{country_code}")

    filter_str = ",".join(filters)
    url = f"{BASE_URL}/works?filter={filter_str}&per_page=1&mailto={MAILTO}"
    data = safe_get(session, url)
    if data:
        return data.get("meta", {}).get("count", 0)
    return None


def compute_metrics_for_journal(session, journal):
    """Compute annual volume and CN author ratio for a journal."""
    source_id = f"https://openalex.org/{journal['openalex_id']}"
    metrics = {
        "issn_l": journal["issn_l"],
        "name": journal["name"],
    }

    for year in YEARS:
        # Total works in year
        total = get_works_count(session, source_id, year)
        metrics[f"volume_{year}"] = total
        time.sleep(0.05)

        # Works with CN authors
        cn_count = get_works_count(session, source_id, year, "CN")
        metrics[f"cn_count_{year}"] = cn_count
        time.sleep(0.05)

    # Compute aggregated metrics
    total_all_years = sum(
        metrics.get(f"volume_{y}", 0) or 0 for y in YEARS
    )
    cn_all_years = sum(
        metrics.get(f"cn_count_{y}", 0) or 0 for y in YEARS
    )

    if total_all_years > 0:
        metrics["cn_author_ratio"] = round(cn_all_years / total_all_years, 4)
    else:
        metrics["cn_author_ratio"] = None

    # Use 2024 as primary annual volume (most complete year)
    metrics["annual_volume_2024"] = metrics.get("volume_2024", 0)
    metrics["annual_volume_2023"] = metrics.get("volume_2023", 0)

    return metrics


def load_journals():
    """Load raw journal lists."""
    journals = []
    for filename in ["sources_economics.json", "sources_demography.json"]:
        filepath = RAW_DIR / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                journals.extend(data)

    # Deduplicate by issn_l
    seen = set()
    unique = []
    for j in journals:
        if j["issn_l"] and j["issn_l"] not in seen:
            seen.add(j["issn_l"])
            unique.append(j)
    return unique


def main():
    session = create_session()
    journals = load_journals()
    print(f"Loaded {len(journals)} unique journals to process")

    # Load existing results for resumability
    existing_metrics = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
            existing_metrics = {m["issn_l"]: m for m in existing_data}
        print(f"Found {len(existing_metrics)} existing metrics (resuming)")

    # Process journals
    results = list(existing_metrics.values())
    remaining = [j for j in journals if j["issn_l"] not in existing_metrics]
    print(f"Remaining to process: {len(remaining)}")

    for i, journal in enumerate(tqdm(remaining, desc="Computing metrics")):
        metrics = compute_metrics_for_journal(session, journal)
        results.append(metrics)

        # Save progress every 50 journals
        if (i + 1) % 50 == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

    # Final save
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved metrics for {len(results)} journals to {OUTPUT_FILE}")

    # Summary stats
    with_cn = [m for m in results if m.get("cn_author_ratio") is not None and m["cn_author_ratio"] > 0]
    print(f"Journals with CN authors: {len(with_cn)}")
    if with_cn:
        avg_cn = sum(m["cn_author_ratio"] for m in with_cn) / len(with_cn)
        print(f"Average CN ratio (among those with CN authors): {avg_cn:.4f}")


if __name__ == "__main__":
    main()
