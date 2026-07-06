#!/usr/bin/env python3
"""
build_embeddings.py - 为期刊数据库生成语义向量索引

流程：
  1. 从 journals_ssci.json 和 aims_scope.json 加载数据
  2. 为每个期刊拼接描述文字（期刊名 + topics + aims_scope + recent_titles 前20条）
  3. 用 allenai/specter2 模型生成嵌入向量
  4. 用 FAISS 建索引并保存

支持断点续传：已生成的向量会缓存到 data/embeddings_cache.json，重启后跳过。

输出：
  data/journal_embeddings.npy   —— 向量矩阵 (N, 768)
  data/journal_index.faiss      —— FAISS IndexFlatIP 索引
  data/journal_index_map.json   —— {位置索引: issn_l} 映射

Usage:
    python3 scripts/build_embeddings.py
    python3 scripts/build_embeddings.py --batch-size 64
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from tqdm import tqdm

DATA_DIR = Path.home() / "journal-finder" / "data"


def load_journals():
    """加载期刊主数据库和 aims_scope 补充数据。"""
    journals_path = DATA_DIR / "journals_ssci.json"
    aims_path = DATA_DIR / "aims_scope.json"

    print(f"加载期刊数据：{journals_path}")
    with open(journals_path, "r", encoding="utf-8") as f:
        journals = json.load(f)

    # 构建 aims_scope 字典，key = issn_l
    # aims_scope.json 格式为 {issn_l: {aims_scope, ...}}
    aims_map = {}
    if aims_path.exists():
        print(f"加载 aims_scope 数据：{aims_path}")
        with open(aims_path, "r", encoding="utf-8") as f:
            aims_data = json.load(f)
        if isinstance(aims_data, dict):
            aims_map = aims_data  # 已经是 {issn_l: {...}} 格式
        else:
            for item in aims_data:
                issn = item.get("issn_l")
                if issn:
                    aims_map[issn] = item

    print(f"共加载 {len(journals)} 个期刊，{len(aims_map)} 条 aims_scope 记录")
    return journals, aims_map


def build_journal_text(journal, aims_map):
    """
    将期刊的多个字段拼接成一段描述文字，用于生成嵌入向量。

    拼接顺序（越靠前权重越大）：
      1. 期刊名称
      2. topics（至多 10 个）
      3. scope_keywords（至多 15 个）
      4. aims_scope 正文（如有）
      5. recent_titles 前 20 条（如有）
    """
    parts = []

    # 1. 期刊名
    name = journal.get("name", "")
    if name:
        parts.append(name)

    # 2. Topics（OpenAlex 主题标签）
    topics = journal.get("topics", [])
    if topics:
        # topics 字段可能是字符串列表，也可能是带 name 键的字典列表
        topic_names = []
        for t in topics[:10]:
            if isinstance(t, dict):
                topic_names.append(t.get("name", ""))
            elif isinstance(t, str):
                topic_names.append(t)
        topic_str = ", ".join(filter(None, topic_names))
        if topic_str:
            parts.append(f"Topics: {topic_str}")

    # 3. scope_keywords
    scope_kws = journal.get("scope_keywords", [])
    if scope_kws:
        parts.append(f"Keywords: {', '.join(scope_kws[:15])}")

    # 4. aims_scope 正文
    issn = journal.get("issn_l")
    if issn and issn in aims_map:
        aims_text = aims_map[issn].get("aims_scope", "")
        if aims_text and len(aims_text) > 20:
            # 截断过长的 aims_scope，避免超出模型 token 限制
            parts.append(aims_text[:1500])

        # 5. recent_titles（文章标题，反映期刊实际内容方向）
        recent = aims_map[issn].get("recent_titles", [])
        if recent:
            titles = [t for t in recent[:20] if t and len(t) > 5]
            if titles:
                parts.append("Recent articles: " + ". ".join(titles))

    return " | ".join(parts)


def load_cache(cache_path):
    """加载断点续传缓存（{issn_l: embedding_list}）。"""
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache, cache_path):
    """保存断点续传缓存。"""
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def build_embeddings(journals, aims_map, batch_size=32):
    """
    对所有期刊生成嵌入向量。

    使用 allenai/specter2（768维），专为学术文献语义相似度设计。
    支持断点续传：已缓存的期刊直接从缓存读取，不重新计算。
    """
    from sentence_transformers import SentenceTransformer

    cache_path = DATA_DIR / "embeddings_cache.json"
    cache = load_cache(cache_path)

    # 过滤出尚未处理的期刊
    todo = [j for j in journals if j["issn_l"] not in cache]
    done = len(journals) - len(todo)
    print(f"断点续传：已完成 {done}/{len(journals)}，待处理 {len(todo)} 个")

    if todo:
        print("加载 specter2 模型（首次运行会下载，约 440MB）...")
        model = SentenceTransformer("allenai/specter2_base")

        # 预估时间
        estimated_batches = (len(todo) + batch_size - 1) // batch_size
        print(f"批次大小：{batch_size}，共 {estimated_batches} 批")

        save_interval = 200  # 每处理 200 个期刊保存一次缓存
        processed_since_save = 0

        texts = []
        issns = []
        for j in todo:
            texts.append(build_journal_text(j, aims_map))
            issns.append(j["issn_l"])

        t0 = time.time()
        for batch_start in tqdm(range(0, len(texts), batch_size), desc="生成嵌入向量"):
            batch_texts = texts[batch_start: batch_start + batch_size]
            batch_issns = issns[batch_start: batch_start + batch_size]

            embeddings = model.encode(
                batch_texts,
                normalize_embeddings=True,  # 归一化后内积等价于余弦相似度
                show_progress_bar=False,
            )

            for issn, emb in zip(batch_issns, embeddings):
                cache[issn] = emb.tolist()

            processed_since_save += len(batch_texts)
            if processed_since_save >= save_interval:
                save_cache(cache, cache_path)
                processed_since_save = 0

        elapsed = time.time() - t0
        print(f"嵌入生成完成，耗时 {elapsed:.1f}s")
        save_cache(cache, cache_path)

    # 按 journals 原始顺序排列向量
    print("整理向量矩阵...")
    valid_journals = []
    embedding_list = []
    for j in journals:
        issn = j["issn_l"]
        if issn in cache:
            valid_journals.append(j)
            embedding_list.append(cache[issn])

    embeddings_matrix = np.array(embedding_list, dtype="float32")
    print(f"向量矩阵形状：{embeddings_matrix.shape}")
    return valid_journals, embeddings_matrix


def build_faiss_index(embeddings_matrix):
    """用 FAISS 建立内积（余弦相似度）索引。"""
    import faiss

    dim = embeddings_matrix.shape[1]
    # IndexFlatIP：精确内积搜索，向量已归一化故等价于余弦相似度
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings_matrix)
    print(f"FAISS 索引建立完成，共 {index.ntotal} 个向量，维度 {dim}")
    return index


def save_artifacts(journals, embeddings_matrix, index):
    """保存三个输出文件。"""
    import faiss

    # 1. 向量矩阵
    emb_path = DATA_DIR / "journal_embeddings.npy"
    np.save(emb_path, embeddings_matrix)
    print(f"已保存：{emb_path}")

    # 2. FAISS 索引
    index_path = DATA_DIR / "journal_index.faiss"
    faiss.write_index(index, str(index_path))
    print(f"已保存：{index_path}")

    # 3. 索引位置 → issn_l 映射
    index_map = {str(i): j["issn_l"] for i, j in enumerate(journals)}
    map_path = DATA_DIR / "journal_index_map.json"
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(index_map, f, ensure_ascii=False)
    print(f"已保存：{map_path}（{len(index_map)} 条映射）")


def main():
    parser = argparse.ArgumentParser(description="为期刊数据库生成语义向量索引")
    parser.add_argument(
        "--batch-size", type=int, default=32,
        help="每批编码的期刊数量（默认 32，GPU 可调大至 128）"
    )
    args = parser.parse_args()

    # 加载数据
    journals, aims_map = load_journals()

    # 生成嵌入向量（支持断点续传）
    valid_journals, embeddings_matrix = build_embeddings(
        journals, aims_map, batch_size=args.batch_size
    )

    # 建立 FAISS 索引
    index = build_faiss_index(embeddings_matrix)

    # 保存输出文件
    save_artifacts(valid_journals, embeddings_matrix, index)

    print("\n全部完成！可运行以下命令测试语义搜索：")
    print("  python3 skill/scripts/semantic_search.py --query 'labor market aging pension' --top 15")


if __name__ == "__main__":
    main()
