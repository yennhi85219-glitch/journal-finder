"""
fetch_review_times.py - 从 Crossref 提取审稿时间线数据

对每个期刊：
1. 从 OpenAlex 获取近期文章的 DOI
2. 查询 Crossref API 提取 received/accepted/published 日期
3. 计算中位数审稿周期

输出：data/raw/review_times.json (keyed by issn_l)
"""

import json
import time
import requests
from datetime import date
from pathlib import Path
from statistics import median
from tqdm import tqdm

BASE_URL_OA = "https://api.openalex.org"
BASE_URL_CR = "https://api.crossref.org"
MAILTO = "test@example.com"

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_FILE = RAW_DIR / "review_times.json"

# How many DOIs to sample per journal
SAMPLE_SIZE = 30


def create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": f"JournalFinder/1.0 (mailto:{MAILTO})",
    })
    return session


def safe_get(session, url, retries=3, delay=0.1):
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
            # 404 or other client errors - don't retry
            if 400 <= resp.status_code < 500:
                return None
        except requests.RequestException:
            time.sleep(2)
    return None


def get_dois_for_journal(session, openalex_id, n=SAMPLE_SIZE):
    """Get recent DOIs for a journal from OpenAlex."""
    source_id = f"https://openalex.org/{openalex_id}"
    url = (
        f"{BASE_URL_OA}/works?"
        f"filter=primary_location.source.id:{source_id},"
        f"publication_year:2023|2024|2025,has_doi:true"
        f"&per_page={n}&sort=publication_date:desc&mailto={MAILTO}"
    )
    data = safe_get(session, url)
    if not data:
        return []
    return [w.get("doi", "").replace("https://doi.org/", "") for w in data.get("results", []) if w.get("doi")]


def parse_date_parts(date_parts):
    """Parse Crossref date-parts [[year, month, day]] into a date object."""
    if not date_parts or not date_parts[0]:
        return None
    parts = date_parts[0]
    if len(parts) >= 3:
        try:
            return date(parts[0], parts[1], parts[2])
        except (ValueError, TypeError):
            return None
    elif len(parts) >= 2:
        try:
            return date(parts[0], parts[1], 1)
        except (ValueError, TypeError):
            return None
    return None


def parse_natural_date(text):
    """Parse natural language date like '21 June 2024' or ISO format '2023-09-19'."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()

    # Try ISO format first (Wiley style: 2023-09-19)
    try:
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        pass

    # Try natural language format (Springer style: 21 June 2024)
    import re
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    match = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if match:
        day, month_str, year = match.groups()
        month_num = months.get(month_str.lower())
        if month_num:
            try:
                return date(int(year), month_num, int(day))
            except ValueError:
                pass

    return None


def extract_dates_from_crossref(work):
    """Extract received, accepted, and published dates from a Crossref work."""
    dates = {}

    # Try assertion array (Springer and Wiley put received/accepted here)
    for assertion in work.get("assertion", []):
        label = assertion.get("label", "").lower()
        name = assertion.get("name", "").lower()
        value = assertion.get("value", "")

        if "received" in label or "received" in name:
            d = parse_natural_date(value)
            if d:
                dates["received"] = d
        elif "accepted" in label or "accepted" in name:
            d = parse_natural_date(value)
            if d:
                dates["accepted"] = d

    # Try created/deposited/published dates
    if "published-online" in work:
        d = parse_date_parts(work["published-online"].get("date-parts"))
        if d:
            dates["published_online"] = d
    elif "published-print" in work:
        d = parse_date_parts(work["published-print"].get("date-parts"))
        if d:
            dates["published_online"] = d

    # Some publishers use 'accepted' directly at top level (date-parts format)
    if "accepted" not in dates and "accepted" in work:
        d = parse_date_parts(work["accepted"].get("date-parts"))
        if d:
            dates["accepted"] = d

    return dates


def compute_review_times(session, journal):
    """Compute review timeline metrics for a single journal."""
    openalex_id = journal["openalex_id"]
    issn_l = journal["issn_l"]

    # Get DOIs
    dois = get_dois_for_journal(session, openalex_id)
    if not dois:
        return {
            "issn_l": issn_l,
            "name": journal["name"],
            "samples": 0,
            "review_median_days": None,
            "accept_to_online_days": None,
        }

    # Query Crossref for each DOI
    review_durations = []  # received -> accepted
    publish_durations = []  # accepted -> published

    for doi in dois:
        url = f"{BASE_URL_CR}/works/{doi}"
        data = safe_get(session, url, delay=0.05)
        if not data:
            time.sleep(0.05)
            continue

        work = data.get("message", {})
        dates = extract_dates_from_crossref(work)

        # Calculate review duration (received -> accepted)
        if "received" in dates and "accepted" in dates:
            delta = (dates["accepted"] - dates["received"]).days
            if 0 < delta < 1500:  # Sanity check: 0-4 years
                review_durations.append(delta)

        # Calculate publication delay (accepted -> published online)
        if "accepted" in dates and "published_online" in dates:
            delta = (dates["published_online"] - dates["accepted"]).days
            if 0 <= delta < 730:  # 0-2 years
                publish_durations.append(delta)

        time.sleep(0.05)  # Rate limit for Crossref

    result = {
        "issn_l": issn_l,
        "name": journal["name"],
        "samples_total": len(dois),
        "samples_with_review": len(review_durations),
        "samples_with_publish": len(publish_durations),
        "review_median_days": int(median(review_durations)) if review_durations else None,
        "review_min_days": min(review_durations) if review_durations else None,
        "review_max_days": max(review_durations) if review_durations else None,
        "accept_to_online_days": int(median(publish_durations)) if publish_durations else None,
    }

    if review_durations:
        result["review_coverage"] = round(len(review_durations) / len(dois), 2)
    else:
        result["review_coverage"] = 0.0

    return result


def load_journals():
    """Load raw journal lists, deduplicate by issn_l."""
    journals = []
    for filename in ["sources_economics.json", "sources_demography.json"]:
        filepath = RAW_DIR / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                journals.extend(json.load(f))

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
    print(f"Loaded {len(journals)} unique journals")

    # Load existing results for resumability
    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
            existing = {r["issn_l"]: r for r in existing_data}
        print(f"Found {len(existing)} existing results (resuming)")

    results = list(existing.values())
    remaining = [j for j in journals if j["issn_l"] not in existing]
    print(f"Remaining to process: {len(remaining)}")

    for i, journal in enumerate(tqdm(remaining, desc="Fetching review times")):
        result = compute_review_times(session, journal)
        results.append(result)

        # Save every 20 journals
        if (i + 1) % 20 == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

    # Final save
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Summary
    with_data = [r for r in results if r.get("review_median_days") is not None]
    print(f"\nSaved {len(results)} journals")
    print(f"Journals with review time data: {len(with_data)} ({len(with_data)/len(results)*100:.1f}%)")
    if with_data:
        avg_review = sum(r["review_median_days"] for r in with_data) / len(with_data)
        print(f"Average median review time: {avg_review:.0f} days")


if __name__ == "__main__":
    main()
