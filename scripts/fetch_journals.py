"""
fetch_journals.py - 从 OpenAlex 拉取经济学和人口学期刊元数据

使用 topic_share.id 过滤策略：
- 经济学：T10785 (Economic Theory and Institutions) | T11742 (Economic Theory and Policy) | T11770 (Fiscal Policy and Economic Growth)
- 人口学：T10585 (Family Dynamics and Relationships) | T12728 (Demographic Trends and Gender Preferences) | T12011 (Insurance, Mortality, Demography, Risk Management) | T11544 (Gender, Labor, and Family Dynamics) | T10209 (Global Maternal and Child Health)

输出：data/raw/sources_economics.json, data/raw/sources_demography.json
"""

import json
import time
import requests
from pathlib import Path

BASE_URL = "https://api.openalex.org"
MAILTO = "test@example.com"  # Replace with actual email for polite pool

# Topic IDs for filtering
ECONOMICS_TOPICS = "T10785|T11742|T11770"
DEMOGRAPHY_TOPICS = "T10585|T12728|T12011|T11544|T10209"

# Minimum works count to filter out tiny/inactive journals
MIN_WORKS_COUNT = 100

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "raw"


def create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": f"JournalFinder/1.0 (mailto:{MAILTO})",
    })
    return session


def fetch_all_sources(session, topic_filter, discipline_name):
    """Paginate through all sources matching the topic filter."""
    all_sources = []
    cursor = "*"
    page = 0

    while cursor:
        url = (
            f"{BASE_URL}/sources?"
            f"filter=type:journal,topic_share.id:{topic_filter},works_count:>{MIN_WORKS_COUNT}"
            f"&per_page=200&cursor={cursor}&mailto={MAILTO}"
        )

        for attempt in range(3):
            try:
                resp = session.get(url, timeout=30)
                if resp.status_code == 200:
                    break
                print(f"  HTTP {resp.status_code}, retry {attempt + 1}")
                time.sleep(2)
            except requests.RequestException as e:
                print(f"  {type(e).__name__}, retry {attempt + 1}")
                time.sleep(3)
        else:
            print(f"  Failed after 3 retries, stopping pagination")
            break

        data = resp.json()
        results = data.get("results", [])
        meta = data.get("meta", {})

        if page == 0:
            print(f"  Total {discipline_name} journals found: {meta.get('count', '?')}")

        all_sources.extend(results)
        cursor = meta.get("next_cursor")
        page += 1

        if page % 5 == 0:
            print(f"  Fetched {len(all_sources)} so far...")

        time.sleep(0.1)  # Polite rate limiting

    return all_sources


def extract_journal_data(source):
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
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = create_session()

    # Fetch Economics journals
    print("Fetching Economics journals...")
    econ_raw = fetch_all_sources(session, ECONOMICS_TOPICS, "Economics")
    econ_journals = [extract_journal_data(s) for s in econ_raw]
    econ_output = OUTPUT_DIR / "sources_economics.json"
    with open(econ_output, "w", encoding="utf-8") as f:
        json.dump(econ_journals, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(econ_journals)} economics journals to {econ_output}")

    time.sleep(1)

    # Fetch Demography journals
    print("\nFetching Demography journals...")
    demo_raw = fetch_all_sources(session, DEMOGRAPHY_TOPICS, "Demography")
    demo_journals = [extract_journal_data(s) for s in demo_raw]
    demo_output = OUTPUT_DIR / "sources_demography.json"
    with open(demo_output, "w", encoding="utf-8") as f:
        json.dump(demo_journals, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(demo_journals)} demography journals to {demo_output}")

    # Summary
    print("\n--- Summary ---")
    print(f"Economics: {len(econ_journals)} journals")
    print(f"Demography: {len(demo_journals)} journals")

    # Spot check: verify known journals are present
    known_economics = {"American Economic Review", "The Quarterly Journal of Economics", "Econometrica"}
    known_demography = {"Demography", "Population and Development Review", "Population Studies"}

    econ_names = {j["name"] for j in econ_journals}
    demo_names = {j["name"] for j in demo_journals}

    print("\nSpot check - Economics:")
    for name in known_economics:
        status = "FOUND" if name in econ_names else "MISSING"
        print(f"  [{status}] {name}")

    print("\nSpot check - Demography:")
    for name in known_demography:
        status = "FOUND" if name in demo_names else "MISSING"
        print(f"  [{status}] {name}")


if __name__ == "__main__":
    main()
