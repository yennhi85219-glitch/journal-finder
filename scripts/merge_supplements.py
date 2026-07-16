#!/usr/bin/env python3
"""
merge_supplements.py — 把手工/Codex 采集的补充数据合并进库文件（不含全库 metrics/review，
那两个由各自的 fetch 脚本 + build_database 负责）。

三个来源：
  1. data/parsed_user_supplements.json  —— 用户手填表格解析出的 {issn: {scope, apc, review_days, accept_online}}
  2. data/codex_cleaned.json            —— Codex 抓取清洗后的 {"scope": {...}, "apc": {...}}

写入目标：
  - aims_scope 正文 → data/manual_webfetch_scope_seed.json（后续 merge + build_embeddings 生效）
  - APC          → data/manual_supplement.json 的 apc_usd
  - 审稿周期      → data/manual_supplement.json 的 review_median_days / accept_to_online_days
                    （标记 review_source=manual，样本数给个占位，避免和 Crossref 抓取的混淆）

幂等：可重复运行；只覆盖本脚本管理的字段。
用 `python`（miniforge），不是 python3。
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"


def load(p, default):
    p = DATA / p
    if p.exists():
        return json.load(open(p, encoding="utf-8"))
    return default


def save(p, obj):
    json.dump(obj, open(DATA / p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def sane_review(rv, ao):
    """审稿天数合理性：投稿→录用 7-730 天；录用→上线 须 < 投稿→录用 且 0-365。"""
    rv = rv if (isinstance(rv, (int, float)) and 7 <= rv <= 730) else None
    if ao is not None and (not (0 <= ao <= 365) or (rv and ao >= rv)):
        ao = None
    return rv, ao


def main():
    user = load("parsed_user_supplements.json", {})
    codex = load("codex_cleaned.json", {"scope": {}, "apc": {}})
    seed = load("manual_webfetch_scope_seed.json", {})
    manual = load("manual_supplement.json", {})

    n_scope = n_apc = n_rev = 0

    # ---- scope：用户表格 + Codex，用户优先（手挑质量通常更高） ----
    scope_sources = []
    for issn, v in codex.get("scope", {}).items():
        scope_sources.append((issn, v["aims_scope"], v.get("source_url", ""), "codex_harvest"))
    for issn, v in user.items():
        if v.get("scope") and len(v["scope"].strip()) >= 80:
            scope_sources.append((issn, v["scope"].strip(), "", "user_csv"))
    # 用户优先：后写覆盖，所以把 user 放后面
    for issn, text, url, src in scope_sources:
        seed[issn] = {
            "aims_scope": text,
            "source": f"supplement:{src}",
            "source_url": url,
            "confidence": "high",
            "last_checked": "2026-07-14",
        }
        n_scope += 1

    # ---- APC ----
    apc_map = {}
    for issn, amt in codex.get("apc", {}).items():
        apc_map[issn] = amt
    for issn, v in user.items():
        if isinstance(v.get("apc"), (int, float)) and 100 <= v["apc"] <= 15000:
            apc_map[issn] = v["apc"]  # 用户优先
    for issn, amt in apc_map.items():
        manual.setdefault(issn, {})
        manual[issn]["apc_usd"] = amt
        n_apc += 1

    # ---- 审稿周期（仅用户表格里有）----
    for issn, v in user.items():
        rv, ao = sane_review(v.get("review_days"), v.get("accept_online"))
        if rv is None:
            continue
        e = manual.setdefault(issn, {})
        e["review_median_days"] = rv
        if ao is not None:
            e["accept_to_online_days"] = ao
        e["review_source"] = "manual_publisher_page"
        n_rev += 1

    save("manual_webfetch_scope_seed.json", seed)
    save("manual_supplement.json", manual)
    print(f"合并完成：scope {n_scope} 条、APC {n_apc} 条、审稿周期 {n_rev} 条")
    print("下一步：python scripts/merge_manual_webfetch_scope_seed.py  # scope 进 aims_scope.json")
    print("        python scripts/build_database.py                    # APC/审稿进主库")
    print("        python scripts/build_embeddings.py                  # 新 scope 重算向量")


if __name__ == "__main__":
    main()
