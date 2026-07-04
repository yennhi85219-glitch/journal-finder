"""
fetch_ssci_journals.py - 基于 JCR SSCI/AHCI 期刊名单，从 OpenAlex 拉取完整元数据

策略变化：不再用 topic_share 过滤（覆盖不全且不稳定），
改为直接用 JCR 2026 的 ISSN 列表作为种子，从 OpenAlex 获取每个期刊的 topics 等详细数据。

输入：JCR Excel 中所有 SSCI/AHCI 期刊的 ISSN
输出：data/raw/sources_ssci_all.json
"""

import json
import time
import requests
import openpyxl
from pathlib import Path
from tqdm import tqdm

BASE_URL = "https://api.openalex.org"
MAILTO = "test@example.com"

JCR_FILE = Path.home() / "Downloads/期刊/2026年度JCR期刊名单（完整版）.xlsx"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "raw"
OUTPUT_FILE = OUTPUT_DIR / "sources_ssci_all.json"


def create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": f"JournalFinder/1.0 (mailto:{MAILTO})",
    })
    return session


def load_ssci_issns():
    """从 JCR Excel 中提取所有 SSCI/AHCI 期刊的 ISSN。"""
    print("Loading JCR 2026 Excel...")
    wb = openpyxl.load_workbook(JCR_FILE, read_only=True)
    ws = wb[wb.sheetnames[0]]

    journals = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        if not row or not row[31]:
            continue
        detail = str(row[31]).upper()
        if "SSCI" not in detail and "AHCI" not in detail:
            continue

        issn = str(row[3]).strip() if row[3] else None
        eissn = str(row[4]).strip() if row[4] else None
        name = str(row[1]).strip() if row[1] else ""
        subject = str(row[6]).strip() if row[6] else ""

        journals.append({
            "name": name,
            "issn": issn,
            "eissn": eissn,
            "subject": subject,
            "jcr_detail": str(row[31]).strip() if row[31] else "",
        })

    wb.close()
    print(f"  Found {len(journals)} SSCI/AHCI journals in JCR")
    return journals


def fetch_source_by_issn(session, issn):
    """Query OpenAlex for a single source by ISSN."""
    url = f"{BASE_URL}/sources?filter=issn:{issn}&mailto={MAILTO}"
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("results"):
                    return data["results"][0]
                return None
            if resp.status_code == 429:
                # Rate limited - wait and retry
                time.sleep(10)
                continue
            if resp.status_code >= 500:
                time.sleep(2)
                continue
        except requests.RequestException:
            time.sleep(2)
    return None


def extract_journal_data(source, jcr_info):
    """Extract relevant fields from an OpenAlex source record."""
    summary = source.get("summary_stats", {})
    topics = source.get("topics", [])

    return {
        "openalex_id": source.get("id", "").split("/")[-1],
        "issn_l": source.get("issn_l"),
        "issn": source.get("issn", []),
        "name": source.get("display_name"),
        "alternate_titles": source.get("alternate_titles", []),
        "publisher": source.get("host_organization_name"),
        "country_code": source.get("country_code"),
        "homepage_url": source.get("homepage_url"),
        "is_oa": source.get("is_oa", False),
        "is_in_doaj": source.get("is_in_doaj", False),
        "apc_usd": source.get("apc_usd"),
        "apc_prices": source.get("apc_prices", []),
        "works_count": source.get("works_count", 0),
        "cited_by_count": source.get("cited_by_count", 0),
        "citedness_2yr": summary.get("2yr_mean_citedness", 0),
        "h_index": summary.get("h_index", 0),
        "topics": [
            {
                "id": t["id"].split("/")[-1],
                "name": t["display_name"],
                "count": t.get("count", 0),
                "subfield": t.get("subfield", {}).get("display_name", ""),
                "field": t.get("field", {}).get("display_name", ""),
            }
            for t in topics[:15]
        ],
        "type": source.get("type"),
        # Carry JCR metadata for later use
        "_jcr_subject": jcr_info.get("subject", ""),
        "_jcr_detail": jcr_info.get("jcr_detail", ""),
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = create_session()

    # Load SSCI ISSNs from JCR
    jcr_journals = load_ssci_issns()

    # Load existing results for resumability
    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
            for j in existing_data:
                if j.get("issn_l"):
                    existing[j["issn_l"]] = j
                # Also index by all ISSNs
                for issn in j.get("issn", []):
                    existing[issn] = j
        print(f"  Found {len(existing_data)} existing records (resuming)")

    results = list({j["issn_l"]: j for j in existing.values() if j.get("issn_l")}.values())
    existing_issns = set(existing.keys())

    # Filter out already-fetched journals
    remaining = []
    for jcr in jcr_journals:
        issn = jcr.get("issn")
        eissn = jcr.get("eissn")
        if issn and issn in existing_issns:
            continue
        if eissn and eissn in existing_issns:
            continue
        remaining.append(jcr)

    print(f"  Remaining to fetch: {len(remaining)}")

    not_found = 0
    for i, jcr in enumerate(tqdm(remaining, desc="Fetching from OpenAlex")):
        # Try ISSN first, then eISSN
        source = None
        if jcr.get("issn"):
            source = fetch_source_by_issn(session, jcr["issn"])
        if not source and jcr.get("eissn"):
            source = fetch_source_by_issn(session, jcr["eissn"])

        if source:
            journal_data = extract_journal_data(source, jcr)
            results.append(journal_data)
            # Update existing index
            if journal_data.get("issn_l"):
                existing_issns.add(journal_data["issn_l"])
            for issn in journal_data.get("issn", []):
                existing_issns.add(issn)
        else:
            not_found += 1

        # Save progress every 100 journals
        if (i + 1) % 100 == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

        time.sleep(0.1)  # Rate limiting

    # Final save
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n--- Summary ---")
    print(f"Total fetched: {len(results)}")
    print(f"Not found in OpenAlex: {not_found}")

    # Subject distribution
    from collections import Counter
    subjects = Counter(j.get("_jcr_subject", "unknown") for j in results)
    print(f"\nTop 10 subjects in fetched data:")
    for subj, count in subjects.most_common(10):
        print(f"  {subj}: {count}")


if __name__ == "__main__":
    main()
