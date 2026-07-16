#!/usr/bin/env python3
"""
merge_aims_scope_txt.py - 将 txt 格式的 aims_scope 补充进 aims_scope.json

txt 格式：期刊名（一行）+ aims_scope 正文（一行或多行）+ 空行分隔
匹配策略：
  1. 精确匹配（大小写不敏感）
  2. 精确匹配失败时，用去除标点/空格的模糊匹配

只补充 json 中 aims_scope 为空的条目，不覆盖已有内容。

Usage:
    python scripts/merge_aims_scope_txt.py
"""

import json
import re
import unicodedata
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
TXT_PATH = Path.home() / "Workbuddy" / "2026-07-06-04-09-21" / "aims_scope_output.txt"


def normalize(text):
    """统一大小写、去除多余空格和标点，用于模糊匹配。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_txt(txt_path):
    """
    解析 txt 文件，返回 [(journal_name, aims_scope_text), ...] 列表。
    格式：名称行 + 正文行（可多行）+ 空行分隔
    """
    entries = []
    with open(txt_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    current_name = None
    current_body = []

    for line in lines:
        line = line.rstrip("\n")

        if not line.strip():
            # 空行：保存当前条目
            if current_name and current_body:
                entries.append((current_name, " ".join(current_body).strip()))
            current_name = None
            current_body = []
            continue

        if current_name is None:
            # 第一行是期刊名
            current_name = line.strip()
        else:
            # 后续行是正文
            current_body.append(line.strip())

    # 文件末尾没有空行的情况
    if current_name and current_body:
        entries.append((current_name, " ".join(current_body).strip()))

    return entries


def build_name_to_issn(journals):
    """构建 {normalized_name: issn_l} 映射。"""
    mapping = {}
    for j in journals:
        key = normalize(j["name"])
        mapping[key] = j["issn_l"]
    return mapping


def main():
    # 加载数据
    print("加载 journals_ssci.json...")
    with open(DATA_DIR / "journals_ssci.json", "r", encoding="utf-8") as f:
        journals = json.load(f)

    print("加载 aims_scope.json...")
    with open(DATA_DIR / "aims_scope.json", "r", encoding="utf-8") as f:
        aims_data = json.load(f)  # {issn_l: {aims_scope, ...}}

    print(f"解析 txt 文件：{TXT_PATH}")
    txt_entries = parse_txt(TXT_PATH)
    print(f"txt 解析出 {len(txt_entries)} 条记录")

    # 构建名称→issn_l 映射
    name_to_issn = build_name_to_issn(journals)

    # 统计
    matched = 0
    skipped_has_content = 0
    skipped_no_match = 0
    unmatched_names = []

    for journal_name, scope_text in txt_entries:
        if not scope_text or len(scope_text) < 20:
            continue

        # 尝试匹配 issn_l
        norm_name = normalize(journal_name)
        issn = name_to_issn.get(norm_name)

        if not issn:
            skipped_no_match += 1
            unmatched_names.append(journal_name)
            continue

        # 检查 json 中是否已有内容
        existing = aims_data.get(issn, {})
        existing_scope = existing.get("aims_scope", "")
        if existing_scope and len(existing_scope) > 20:
            skipped_has_content += 1
            continue

        # 补充内容
        if issn not in aims_data:
            aims_data[issn] = {"aims_scope": "", "abstract_summary": "", "recent_titles": [], "source": ""}
        aims_data[issn]["aims_scope"] = scope_text
        aims_data[issn]["source"] = aims_data[issn].get("source", "") or "txt_import"
        matched += 1

    # 保存
    out_path = DATA_DIR / "aims_scope.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(aims_data, f, ensure_ascii=False, indent=2)

    print(f"\n完成：")
    print(f"  成功补充：{matched} 条")
    print(f"  已有内容跳过：{skipped_has_content} 条")
    print(f"  未匹配到 issn_l：{skipped_no_match} 条")

    # 输出未匹配的期刊名供参考
    if unmatched_names:
        unmatched_path = DATA_DIR / "unmatched_names.txt"
        with open(unmatched_path, "w", encoding="utf-8") as f:
            f.write("\n".join(unmatched_names))
        print(f"  未匹配名单已保存至：{unmatched_path}")

    # 最终统计
    total_with_scope = sum(
        1 for v in aims_data.values()
        if v.get("aims_scope") and len(v.get("aims_scope", "")) > 20
    )
    print(f"\naims_scope.json 现有有效条数：{total_with_scope} / {len(aims_data)}")


if __name__ == "__main__":
    main()
