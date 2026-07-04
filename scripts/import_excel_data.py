"""
import_excel_data.py - 从 JCR 和中科院分区 Excel 导入数据到 manual_supplement.json

数据源：
- /Users/a86166/Downloads/期刊/2026年度JCR期刊名单（完整版）.xlsx
- /Users/a86166/Downloads/期刊/2025中科院分区表完整版（附2023vs2025对比版）.xlsx

匹配逻辑：通过 ISSN 与我们数据库中的期刊匹配
"""

import json
import openpyxl
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
JCR_FILE = Path.home() / "Downloads/期刊/2026年度JCR期刊名单（完整版）.xlsx"
CAS_FILE = Path.home() / "Downloads/期刊/2025中科院分区表完整版（附2023vs2025对比版）.xlsx"


def load_our_journals():
    """Load all ISSNs from our database to know which journals to match."""
    issn_to_journal = {}  # issn -> issn_l mapping
    issn_l_to_name = {}

    for filename in ["journals_economics.json", "journals_demography.json"]:
        filepath = DATA_DIR / filename
        if not filepath.exists():
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            journals = json.load(f)
            for j in journals:
                issn_l = j.get("issn_l")
                if not issn_l:
                    continue
                issn_l_to_name[issn_l] = j.get("name", "")
                # Map all known ISSNs for this journal to its issn_l
                all_issns = j.get("issn", []) if isinstance(j.get("issn"), list) else []
                if issn_l:
                    all_issns.append(issn_l)
                for issn in all_issns:
                    if issn:
                        issn_to_journal[issn.strip().upper()] = issn_l

    return issn_to_journal, issn_l_to_name


def load_our_journal_names():
    """Load journal name -> issn_l mapping for name-based matching."""
    name_to_issn_l = {}
    for filename in ["journals_economics.json", "journals_demography.json"]:
        filepath = DATA_DIR / filename
        if not filepath.exists():
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            journals = json.load(f)
            for j in journals:
                name = j.get("name", "").upper().strip()
                issn_l = j.get("issn_l")
                if name and issn_l:
                    name_to_issn_l[name] = issn_l
                # Also alternate titles
                for alt in j.get("alternate_titles", []) or []:
                    if alt:
                        name_to_issn_l[alt.upper().strip()] = issn_l
    return name_to_issn_l


def import_jcr():
    """Import JCR data - extract ISSN, IF, JIF quartile, publisher, subject category."""
    print("Loading JCR 2026 data...")
    wb = openpyxl.load_workbook(JCR_FILE, read_only=True)
    ws = wb[wb.sheetnames[0]]

    jcr_data = {}  # issn -> record

    for row in ws.iter_rows(min_row=4, values_only=True):
        # Columns: 0=rank, 1=name, 2=abbr, 3=ISSN, 4=eISSN, 5=publisher, 6=subject,
        # 7=JIF, 8=JIF_quartile, ...
        if not row or not row[3]:
            continue

        issn = str(row[3]).strip().upper() if row[3] else None
        eissn = str(row[4]).strip().upper() if row[4] else None
        name = str(row[1]).strip() if row[1] else ""

        record = {
            "name": name,
            "abbreviation": str(row[2]).strip() if row[2] else None,
            "publisher": str(row[5]).strip() if row[5] else None,
            "subject_category": str(row[6]).strip() if row[6] else None,
            "impact_factor": float(row[7]) if row[7] and row[7] != "N/A" else None,
            "jcr_quartile": str(row[8]).strip() if row[8] else None,
            "jif_percentile": float(row[9]) if row[9] else None,
            "five_year_if": float(row[15]) if row[15] else None,
            "gold_oa_pct": float(row[29]) if row[29] else None,
            "subject_detail": str(row[31]).strip() if row[31] else None,
        }

        if issn:
            jcr_data[issn] = record
        if eissn:
            jcr_data[eissn] = record

    wb.close()
    print(f"  Loaded {len(jcr_data)} ISSN entries from JCR")
    return jcr_data


def import_cas():
    """Import CAS zoning data - match by journal name."""
    print("Loading CAS 2025 zoning data...")
    wb = openpyxl.load_workbook(CAS_FILE, read_only=True)

    # Use '完整版' sheet
    ws = wb['完整版']
    # Headers: 期刊名称, 2025分区, Top, Open Access

    cas_data = {}  # upper name -> record

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        name = str(row[0]).strip().upper()
        cas_data[name] = {
            "cas_zone": int(row[1]) if row[1] else None,
            "is_top": str(row[2]).strip() == "是" if row[2] else False,
            "is_oa": str(row[3]).strip() == "是" if row[3] else False,
        }

    wb.close()

    # Also load comparison sheet for more data
    wb2 = openpyxl.load_workbook(CAS_FILE, read_only=True)
    ws2 = wb2['2023 vs 2025分区对比']
    for row in ws2.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        name = str(row[0]).strip().upper()
        if name not in cas_data:
            cas_data[name] = {
                "cas_zone": int(row[1]) if row[1] else None,
                "is_top": str(row[3]).strip() == "是" if row[3] else False,
                "is_oa": str(row[4]).strip() == "是" if row[4] else False,
            }
    wb2.close()

    print(f"  Loaded {len(cas_data)} journal entries from CAS")
    return cas_data


def main():
    # Load our journal database ISSNs and names
    issn_to_issn_l, issn_l_to_name = load_our_journals()
    name_to_issn_l = load_our_journal_names()
    print(f"Our database: {len(issn_l_to_name)} journals, {len(issn_to_issn_l)} ISSNs indexed")

    # Load existing supplement
    supplement_path = DATA_DIR / "manual_supplement.json"
    if supplement_path.exists():
        with open(supplement_path, "r", encoding="utf-8") as f:
            supplement = json.load(f)
    else:
        supplement = {}
    print(f"Existing supplement: {len(supplement)} entries")

    # Import JCR
    jcr_data = import_jcr()

    # Import CAS
    cas_data = import_cas()

    # Match JCR data to our journals
    jcr_matched = 0
    for issn, jcr_record in jcr_data.items():
        issn_clean = issn.replace("-", "").strip()
        issn_with_dash = issn.strip()

        # Try to find this ISSN in our database
        issn_l = issn_to_issn_l.get(issn_with_dash)
        if not issn_l:
            issn_l = issn_to_issn_l.get(issn_clean)
        if not issn_l:
            continue

        # Found a match - update supplement
        if issn_l not in supplement:
            supplement[issn_l] = {}

        entry = supplement[issn_l]
        if jcr_record.get("impact_factor") is not None:
            entry["impact_factor"] = jcr_record["impact_factor"]
        if jcr_record.get("jcr_quartile"):
            entry["jcr_quartile"] = jcr_record["jcr_quartile"]
        if jcr_record.get("subject_category"):
            entry["subject_category"] = jcr_record["subject_category"]
        if jcr_record.get("subject_detail"):
            entry["subject_detail"] = jcr_record["subject_detail"]
        if jcr_record.get("five_year_if") is not None:
            entry["five_year_if"] = jcr_record["five_year_if"]
        if jcr_record.get("gold_oa_pct") is not None:
            entry["gold_oa_pct"] = jcr_record["gold_oa_pct"]

        entry["last_verified"] = "2026-07"
        jcr_matched += 1

    print(f"\nJCR matched: {jcr_matched} journals")

    # Match CAS data to our journals (by name)
    cas_matched = 0
    for cas_name, cas_record in cas_data.items():
        issn_l = name_to_issn_l.get(cas_name)
        if not issn_l:
            # Try partial matching for common variations
            # e.g., "THE QUARTERLY JOURNAL OF ECONOMICS" vs "QUARTERLY JOURNAL OF ECONOMICS"
            for prefix in ["THE ", "A "]:
                if cas_name.startswith(prefix):
                    issn_l = name_to_issn_l.get(cas_name[len(prefix):])
                    if issn_l:
                        break
            if not issn_l:
                # Try adding "THE"
                issn_l = name_to_issn_l.get("THE " + cas_name)
        if not issn_l:
            continue

        if issn_l not in supplement:
            supplement[issn_l] = {}

        entry = supplement[issn_l]
        if cas_record.get("cas_zone") is not None:
            entry["cas_zone"] = cas_record["cas_zone"]
        if cas_record.get("is_top"):
            entry["cas_top"] = True
        cas_matched += 1

    print(f"CAS matched: {cas_matched} journals")

    # Ensure all entries have required fields
    for issn_l, entry in supplement.items():
        entry.setdefault("jcr_quartile", None)
        entry.setdefault("cas_zone", None)
        entry.setdefault("impact_factor", None)
        entry.setdefault("apc_usd", None)
        entry.setdefault("apc_waiver", None)
        entry.setdefault("oa_type", None)
        entry.setdefault("word_limit_min", None)
        entry.setdefault("word_limit_max", None)
        entry.setdefault("review_type", "double_blind")
        entry.setdefault("warning_tags", [])
        entry.setdefault("notes", "")
        entry.setdefault("last_verified", "2026-07")

    # Save
    with open(supplement_path, "w", encoding="utf-8") as f:
        json.dump(supplement, f, ensure_ascii=False, indent=2)

    print(f"\nFinal supplement: {len(supplement)} entries saved to {supplement_path}")

    # Show some examples
    print("\n--- Sample entries ---")
    sample_issns = ["0002-8282", "0033-5533", "0070-3370", "0933-1433"]
    for issn_l in sample_issns:
        if issn_l in supplement:
            e = supplement[issn_l]
            name = issn_l_to_name.get(issn_l, "?")
            print(f"  {name}: JCR={e.get('jcr_quartile')}, CAS={e.get('cas_zone')}, IF={e.get('impact_factor')}")


if __name__ == "__main__":
    main()
