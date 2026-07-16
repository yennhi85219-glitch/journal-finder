#!/usr/bin/env python3
"""
build_embeddings.py - 为期刊数据库生成语义向量索引

流程：
  1. 从 journals_ssci.json 和 aims_scope.json 加载数据
  2. 为每个期刊拼接描述文字（期刊名 + topics + aims_scope + recent_titles 前20条）
  3. 用 allenai/specter2 模型生成嵌入向量
  4. 用 FAISS 建索引并保存

支持断点续传：已生成的向量会缓存到 data/embeddings_cache.json，重启后跳过。
缓存按「期刊描述文字的哈希」判断，因此更新期刊的 aims_scope / topics 后重跑会自动重算受影响的期刊（旧版按 issn_l 判断会漏掉文本变更）。

输出：
  data/journal_embeddings.npy   —— 向量矩阵 (N, 768)
  data/journal_index.faiss      —— FAISS IndexFlatIP 索引
  data/journal_index_map.json   —— {位置索引: issn_l} 映射

Usage:
    python scripts/build_embeddings.py
    python scripts/build_embeddings.py --batch-size 64
"""

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
import sys

import numpy as np
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from runtime_paths import resolve_data_dir


DATA_DIR = None
MODEL_ID = "allenai/specter2_base"
CACHE_SCHEMA_VERSION = 2
EXPECTED_EMBEDDING_DIM = 768


def set_data_dir(path=None):
    global DATA_DIR
    DATA_DIR = resolve_data_dir(__file__, explicit=path)
    return DATA_DIR


def get_data_dir():
    return DATA_DIR or set_data_dir()


def load_journals():
    """加载期刊主数据库和 aims_scope 补充数据。"""
    data_dir = get_data_dir()
    journals_path = data_dir / "journals_ssci.json"
    aims_path = data_dir / "aims_scope.json"
    manual_seed_path = data_dir / "manual_webfetch_scope_seed.json"

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

    # Overlay manually web-fetched official scope records so they survive
    # generator reruns or external rewrites of aims_scope.json.
    if manual_seed_path.exists():
        print(f"加载手工 webfetch seed：{manual_seed_path}")
        with open(manual_seed_path, "r", encoding="utf-8") as f:
            manual_seed = json.load(f)
        if isinstance(manual_seed, dict):
            for issn, patch in manual_seed.items():
                existing = aims_map.get(issn, {})
                if not isinstance(existing, dict):
                    existing = {}
                merged = existing.copy()
                merged.update(patch)
                aims_map[issn] = merged

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
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: 忽略损坏的 embedding cache：{exc}")
    return {}


def save_json_atomic(data, path, *, ensure_ascii=True):
    """Write JSON through a sibling temp file so interruption preserves the old file."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(data, f, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def file_sha256(path):
    """Return a streaming SHA256 checksum for an artifact file."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_cache(cache, cache_path):
    """保存断点续传缓存。"""
    save_json_atomic(cache, cache_path)


def valid_embedding(embedding):
    """Check one cached/model vector before it can enter the index."""
    try:
        vector = np.asarray(embedding, dtype="float32")
    except (TypeError, ValueError):
        return False
    if vector.shape != (EXPECTED_EMBEDDING_DIM,) or not np.isfinite(vector).all():
        return False
    return abs(float(np.linalg.norm(vector)) - 1.0) <= 1e-3


def validate_embedding_matrix(matrix, expected_rows, label):
    """Reject incomplete, malformed, or non-normalized embedding batches."""
    if matrix.ndim != 2 or matrix.shape != (expected_rows, EXPECTED_EMBEDDING_DIM):
        raise ValueError(
            f"{label}: expected {(expected_rows, EXPECTED_EMBEDDING_DIM)}, "
            f"got {matrix.shape}"
        )
    if not np.isfinite(matrix).all():
        raise ValueError(f"{label}: embedding matrix contains NaN/Inf")
    norms = np.linalg.norm(matrix, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        raise ValueError(f"{label}: embeddings are not normalized")


def build_embeddings(journals, aims_map, batch_size=32):
    """
    对所有期刊生成嵌入向量。

    使用 allenai/specter2（768维），专为学术文献语义相似度设计。
    支持断点续传：已缓存的期刊直接从缓存读取，不重新计算。
    """
    from sentence_transformers import SentenceTransformer

    cache_path = get_data_dir() / "embeddings_cache.json"
    cache = load_cache(cache_path)

    def text_hash(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def cached_emb(issn, thash):
        """Return the cached embedding for this issn IFF the text hash matches.

        Tolerates the legacy format (bare embedding list, no hash) by treating
        it as a miss so stale vectors get recomputed once."""
        entry = cache.get(issn)
        if (
            isinstance(entry, dict)
            and entry.get("v") == CACHE_SCHEMA_VERSION
            and entry.get("model") == MODEL_ID
            and entry.get("h") == thash
            and valid_embedding(entry.get("emb"))
        ):
            return entry.get("emb")
        return None

    # 预先算好每本期刊的描述文字与哈希，据此判断缓存是否命中
    texts_by_issn = {}
    hashes_by_issn = {}
    for j in journals:
        issn = j["issn_l"]
        t = build_journal_text(j, aims_map)
        texts_by_issn[issn] = t
        hashes_by_issn[issn] = text_hash(t)

    # One-time upgrade of the immediately preceding cache schema. That schema
    # was produced by this same hard-coded model but lacked explicit metadata.
    upgraded_cache_count = 0
    for issn, thash in hashes_by_issn.items():
        entry = cache.get(issn)
        if (
            isinstance(entry, dict)
            and set(entry) == {"h", "emb"}
            and entry.get("h") == thash
            and valid_embedding(entry.get("emb"))
        ):
            cache[issn] = {
                "v": CACHE_SCHEMA_VERSION,
                "model": MODEL_ID,
                "h": thash,
                "emb": entry["emb"],
            }
            upgraded_cache_count += 1
    if upgraded_cache_count:
        print(f"升级 embedding cache schema：{upgraded_cache_count} 条")

    current_issns = set(texts_by_issn)
    stale_cache_count = len(set(cache) - current_issns)
    if stale_cache_count:
        cache = {issn: entry for issn, entry in cache.items() if issn in current_issns}
        print(f"清理非 canonical 缓存：{stale_cache_count} 条")

    todo = [j for j in journals if cached_emb(j["issn_l"], hashes_by_issn[j["issn_l"]]) is None]
    done = len(journals) - len(todo)
    print(f"断点续传：已完成 {done}/{len(journals)}，待处理 {len(todo)} 个（按文本哈希判断）")

    if todo:
        print("加载 specter2 模型（首次运行会下载，约 440MB）...")
        model = SentenceTransformer(MODEL_ID)

        # 预估时间
        estimated_batches = (len(todo) + batch_size - 1) // batch_size
        print(f"批次大小：{batch_size}，共 {estimated_batches} 批")

        save_interval = 200  # 每处理 200 个期刊保存一次缓存
        processed_since_save = 0

        issns = [j["issn_l"] for j in todo]
        texts = [texts_by_issn[i] for i in issns]

        t0 = time.time()
        for batch_start in tqdm(range(0, len(texts), batch_size), desc="生成嵌入向量"):
            batch_texts = texts[batch_start: batch_start + batch_size]
            batch_issns = issns[batch_start: batch_start + batch_size]

            embeddings = model.encode(
                batch_texts,
                normalize_embeddings=True,  # 归一化后内积等价于余弦相似度
                show_progress_bar=False,
            )
            embeddings = np.asarray(embeddings, dtype="float32")
            validate_embedding_matrix(
                embeddings,
                len(batch_issns),
                f"batch starting at {batch_start}",
            )

            for issn, emb in zip(batch_issns, embeddings):
                cache[issn] = {
                    "v": CACHE_SCHEMA_VERSION,
                    "model": MODEL_ID,
                    "h": hashes_by_issn[issn],
                    "emb": emb.tolist(),
                }

            processed_since_save += len(batch_texts)
            if processed_since_save >= save_interval:
                save_cache(cache, cache_path)
                processed_since_save = 0

        elapsed = time.time() - t0
        print(f"嵌入生成完成，耗时 {elapsed:.1f}s")
        save_cache(cache, cache_path)
    elif stale_cache_count or upgraded_cache_count:
        save_cache(cache, cache_path)

    # 按 journals 原始顺序排列向量
    print("整理向量矩阵...")
    valid_journals = []
    embedding_list = []
    for j in journals:
        issn = j["issn_l"]
        emb = cached_emb(issn, hashes_by_issn[issn])
        if emb is not None:
            valid_journals.append(j)
            embedding_list.append(emb)

    embeddings_matrix = np.array(embedding_list, dtype="float32")
    if len(valid_journals) != len(journals):
        raise RuntimeError(
            f"Refusing to publish incomplete embeddings: "
            f"{len(valid_journals)}/{len(journals)} journals"
        )
    validate_embedding_matrix(
        embeddings_matrix,
        len(journals),
        "final embedding matrix",
    )
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
    """Atomically replace each output after all new artifacts are ready."""
    import faiss

    data_dir = get_data_dir()
    emb_path = data_dir / "journal_embeddings.npy"
    index_path = data_dir / "journal_index.faiss"
    index_map = {str(i): j["issn_l"] for i, j in enumerate(journals)}
    map_path = data_dir / "journal_index_map.json"
    meta_path = data_dir / "journal_index_meta.json"

    emb_temp = index_temp = map_temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=emb_path.parent, prefix=f".{emb_path.name}.", delete=False
        ) as f:
            emb_temp = Path(f.name)
            np.save(f, embeddings_matrix)
            f.flush()
            os.fsync(f.fileno())

        with tempfile.NamedTemporaryFile(
            "wb", dir=index_path.parent, prefix=f".{index_path.name}.", delete=False
        ) as f:
            index_temp = Path(f.name)
        faiss.write_index(index, str(index_temp))
        with open(index_temp, "rb+") as f:
            os.fsync(f.fileno())

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=map_path.parent,
            prefix=f".{map_path.name}.",
            delete=False,
        ) as f:
            json.dump(index_map, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
            map_temp = Path(f.name)

        manifest = {
            "version": 1,
            "journal_count": len(index_map),
            "embedding_dim": int(embeddings_matrix.shape[1]),
            "model_id": MODEL_ID,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "ordered_issn_sha256": hashlib.sha256(
                "\n".join(index_map.values()).encode("utf-8")
            ).hexdigest(),
            "index_sha256": file_sha256(index_temp),
            "map_sha256": file_sha256(map_temp),
        }

        # The manifest is the commit marker. If interruption occurs after one
        # artifact is replaced, its checksum will not match the old manifest.
        emb_temp.replace(emb_path)
        index_temp.replace(index_path)
        map_temp.replace(map_path)
        save_json_atomic(manifest, meta_path, ensure_ascii=False)
    finally:
        for temp_path in (emb_temp, index_temp, map_temp):
            if temp_path and temp_path.exists():
                temp_path.unlink()

    print(f"已保存：{emb_path}")
    print(f"已保存：{index_path}")
    print(f"已保存：{map_path}（{len(index_map)} 条映射）")
    print(f"已保存：{meta_path}")


def main():
    parser = argparse.ArgumentParser(description="为期刊数据库生成语义向量索引")
    parser.add_argument(
        "--batch-size", type=int, default=32,
        help="每批编码的期刊数量（默认 32，GPU 可调大至 128）"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing journals_ssci.json",
    )
    args = parser.parse_args()
    set_data_dir(args.data_dir)

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
    print("  python skill/scripts/semantic_search.py --query 'labor market aging pension' --top 15")


if __name__ == "__main__":
    main()
