# -*- coding: utf-8 -*-
"""
训练数据文件管理 — NN 专用的干净数据格式

设计原则:
  - V2.1 的 tier1/tier2 是程序内部格式（含 PMI/auto_score/sessions 元数据）
  - NN 只需要: bucket → patterns → keywords（桶聚合格式，方便人工 review）
  - 按哈希分离存储，人工审核后再合并，保证训练数据质量

文件体系:
  nn/_storage/
  ├── training/                  ← 按哈希分离的"生"训练数据（需人工审核）
  │   ├── {hash1}.json           ← 每份序时账一个文件
  │   └── {hash2}.json
  ├── training_data.json          ← 合并后的训练数据（仅从已审核的哈希文件合并）
  ├── best_model.pt               ← [训练产物] PyTorch checkpoint
  ├── pattern_vectors.json        ← [训练产物] Pattern 向量表
  ├── keyword_vectors.json        ← [训练产物] Keyword 向量表
  └── training_log.json           ← [训练产物] 训练日志

单哈希文件格式 (training/{hash}.json):
  {
    "fingerprint": "abc123",
    "created_at": "2025-07-25",
    "reviewed": false,              ← 是否已审核
    "buckets": {
      "费用报销": {
        "patterns": [
          "管理费用|借, 银行存款|贷",
          "销售费用|借, 银行存款|贷"
        ],
        "keywords": [
          "备用金", "滴滴", "办公费", "顺丰", "快递费"
        ]
      },
      "存货采购": { ... }
    }
  }

合并文件格式 (training_data.json):
  {
    "updated_at": "...",
    "source_hashes": ["abc123", "def456"],
    "buckets": { ... }
  }
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from .vocab import VocabManager


# ============================================================================
# 构建单哈希训练数据（桶聚合格式）
# ============================================================================

def build_hash_training_data(
    df: pd.DataFrame,
    column_mapping: Dict[str, str],
    fingerprint: str = "",
    reviewed_keywords: Dict[str, List[str]] = None,
    output_dir: str = None,
) -> Dict[str, Any]:
    """从 V2.1 分类结果按桶聚合 patterns + keywords，写入单哈希文件。

    输出格式（按桶聚合，方便人工 review）:
    {
      "fingerprint": "...",
      "buckets": {
        "费用报销": {
          "patterns": ["管理费用|借, 银行存款|贷", ...],
          "keywords": ["备用金", "滴滴", "办公费", ...]
        }
      }
    }

    Args:
        df: V2.1 classify() 输出（含「业务分类」列）
        column_mapping: {voucher_no, subject, subject_name, summary, debit, credit}
        fingerprint: 序时账哈希指纹
        reviewed_keywords: 关键词白名单 {bucket: [word]}（用于过滤）
        output_dir: 输出目录（默认 nn/_storage/training/）

    Returns:
        训练数据 dict
    """
    from .data import extract_voucher_data

    if output_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_dir = str(Path(NN_STORAGE_DIR) / "training")

    # 提取凭证级数据（已去重 keywords）
    voucher_records = extract_voucher_data(
        df, column_mapping, reviewed_keywords=reviewed_keywords,
    )

    # ── 按桶聚合 ──
    buckets: Dict[str, Dict[str, Set[str]]] = defaultdict(
        lambda: {"patterns": set(), "keywords": set()}
    )

    from .data import format_pattern_list

    for vr in voucher_records:
        bucket = vr["bucket"]
        # patterns: " | " 分隔，比逗号更清晰
        patterns_str = format_pattern_list(vr["patterns"])
        buckets[bucket]["patterns"].add(patterns_str)
        # keywords: 去重
        for kw in vr["keywords"]:
            buckets[bucket]["keywords"].add(kw)

    # 转为可序列化格式（set → sorted list）
    buckets_data = {}
    for bucket in sorted(buckets.keys()):
        buckets_data[bucket] = {
            "patterns": sorted(buckets[bucket]["patterns"]),
            "keywords": sorted(buckets[bucket]["keywords"]),
        }

    data = {
        "fingerprint": fingerprint or "unknown",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reviewed": False,
        "buckets": buckets_data,
    }

    # 写入文件
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{fingerprint}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total_kw = sum(len(b["keywords"]) for b in buckets_data.values())
    total_pat = sum(len(b["patterns"]) for b in buckets_data.values())
    print(f"[OK] 训练数据已保存: {path}")
    print(f"   {len(buckets_data)} 桶, {total_pat} patterns, {total_kw} keywords")

    return data


# ============================================================================
# 合并所有已审核的哈希文件
# ============================================================================

def merge_training_data(
    training_dir: str = None,
    only_reviewed: bool = True,
    output_path: str = None,
) -> Dict[str, Any]:
    """合并所有（已审核的）哈希训练文件为一份训练数据。

    Args:
        training_dir: 哈希文件目录（默认 nn/_storage/training/）
        only_reviewed: True = 只合并已审核的，False = 全部合并
        output_path: 合并输出路径（默认 nn/_storage/training_data.json）

    Returns:
        合并后的训练数据
    """
    if training_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        training_dir = str(Path(NN_STORAGE_DIR) / "training")
    if output_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_path = str(Path(NN_STORAGE_DIR) / "training_data.json")

    training_dir = Path(training_dir)

    if not training_dir.exists():
        print(f"[WARN] training 目录不存在: {training_dir}")
        return {"buckets": {}, "source_hashes": []}

    # ── 合并 ──
    buckets: Dict[str, Dict[str, set]] = defaultdict(
        lambda: {"patterns": set(), "keywords": set()}
    )
    source_hashes = []
    skipped = 0

    for f in sorted(training_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            skipped += 1
            continue

        if only_reviewed and not data.get("reviewed", False):
            skipped += 1
            continue

        source_hashes.append(data.get("fingerprint", f.stem))

        for bucket, bucket_data in data.get("buckets", {}).items():
            for p in bucket_data.get("patterns", []):
                buckets[bucket]["patterns"].add(p)
            for kw in bucket_data.get("keywords", []):
                buckets[bucket]["keywords"].add(kw)

    # 转可序列化
    buckets_data = {}
    for bucket in sorted(buckets.keys()):
        buckets_data[bucket] = {
            "patterns": sorted(buckets[bucket]["patterns"]),
            "keywords": sorted(buckets[bucket]["keywords"]),
        }

    merged = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_hashes": source_hashes,
        "total_hashes": len(source_hashes),
        "skipped_unreviewed": skipped,
        "buckets": buckets_data,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    total_kw = sum(len(b["keywords"]) for b in buckets_data.values())
    total_pat = sum(len(b["patterns"]) for b in buckets_data.values())
    print(f"[OK] 合并训练数据已保存: {output_path}")
    print(f"   {len(source_hashes)} 哈希, {len(buckets_data)} 桶, "
          f"{total_pat} patterns, {total_kw} keywords")
    if skipped:
        print(f"   跳过 {skipped} 个未审核的哈希文件")

    return merged


# ============================================================================
# 加载训练数据 → TrainingDataset
# ============================================================================

def load_merged_training_data(
    file_path: str = None,
    vocab: VocabManager = None,
    bucket_filter: Set[str] = None,
) -> Tuple["TrainingDataset", VocabManager, Dict[str, int], Dict[str, Any]]:
    """从合并后的 training_data.json 加载 → 构建 TrainingDataset。

    合并格式 {buckets: {bucket: {patterns: [...], keywords: [...]}}}
    需要展开为逐凭证记录（每个 pattern 字符串 = 一张虚拟凭证）。
    """
    from .data import TrainingDataset

    if file_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        file_path = str(Path(NN_STORAGE_DIR) / "training_data.json")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"训练数据不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if vocab is None:
        vocab = VocabManager()

    records = []
    all_buckets = set()

    for bucket, bucket_data in data.get("buckets", {}).items():
        if bucket_filter and bucket not in bucket_filter:
            continue
        if bucket in ("未分类", "无法分类"):
            continue

        patterns_list = bucket_data.get("patterns", [])
        keywords_list = bucket_data.get("keywords", [])

        if not patterns_list:
            continue

        all_buckets.add(bucket)

        # 先把关键词注册到词表（否则 lookup 不到）
        vocab.add_keywords(keywords_list)

        # 每个 pattern 字符串 = 一个完整的科目组合（如 "管理费用|借 | 银行存款|贷"）
        # 它是一个整体，不是多个独立 pattern 的拆分
        for pat_str in patterns_list:
            pattern_id = vocab.get_or_add_pattern(pat_str)
            keyword_ids = vocab.lookup_keywords_strict(keywords_list)

            records.append({
                "voucher_id": f"{bucket}::{pat_str[:60]}",
                "bucket": bucket,
                "pattern_ids": [pattern_id],  # 完整 pattern，非拆分
                "keyword_ids": keyword_ids,
            })

    bucket_to_idx = {b: i for i, b in enumerate(sorted(all_buckets))}
    dataset = TrainingDataset(records, bucket_to_idx)

    metadata = {
        "source_hashes": data.get("source_hashes", []),
        "updated_at": data.get("updated_at", ""),
        "loaded_records": len(records),
        "buckets": len(all_buckets),
        "vocab": vocab.stats(),
    }

    print(f"[OK] 已加载训练数据: {len(records)} 条虚拟凭证, "
          f"{len(all_buckets)} 桶, {vocab}")

    return dataset, vocab, bucket_to_idx, metadata


# ============================================================================
# 查看 / 管理
# ============================================================================

def list_training_files(training_dir: str = None) -> List[Dict]:
    """列出所有哈希训练文件及其审核状态。"""
    if training_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        training_dir = str(Path(NN_STORAGE_DIR) / "training")

    training_dir = Path(training_dir)
    if not training_dir.exists():
        return []

    files = []
    for f in sorted(training_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reviewed = data.get("reviewed", False)
            buckets = data.get("buckets", {})
            total_kw = sum(len(b.get("keywords", [])) for b in buckets.values())
            total_pat = sum(len(b.get("patterns", [])) for b in buckets.values())
            files.append({
                "filename": f.name,
                "fingerprint": data.get("fingerprint", ""),
                "reviewed": reviewed,
                "buckets": len(buckets),
                "patterns": total_pat,
                "keywords": total_kw,
                "created_at": data.get("created_at", ""),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        except (json.JSONDecodeError, KeyError):
            pass

    return files


def mark_reviewed(fingerprint: str, reviewed: bool = True,
                  training_dir: str = None) -> bool:
    """标记某哈希文件为已审核/未审核。"""
    if training_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        training_dir = str(Path(NN_STORAGE_DIR) / "training")

    path = Path(training_dir) / f"{fingerprint}.json"
    if not path.exists():
        print(f"[WARN] 文件不存在: {path}")
        return False

    data = json.loads(path.read_text(encoding="utf-8"))
    data["reviewed"] = reviewed
    data["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if reviewed else ""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    status = "已审核" if reviewed else "待审核"
    print(f"[OK] {path.name} → {status}")
    return True


# ============================================================================
# 向量表导出（训练后）
# ============================================================================

def export_vector_tables(model, vocab: VocabManager, output_dir: str):
    """训练后，将 Pattern 和 Keyword 向量导出为人类可读的 JSON。"""
    import torch

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Pattern 向量表
    pattern_vecs = {}
    for i in range(vocab.num_patterns):
        vec = model.pattern_emb.weight[i].detach().cpu().tolist()
        pattern_vecs[vocab._id_to_pattern.get(i, f"?_{i}")] = [round(v, 6) for v in vec]

    pattern_data = {"dim": model.dim, "count": len(pattern_vecs),
                    "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "vectors": pattern_vecs}
    pp = output_dir / "pattern_vectors.json"
    with open(pp, "w", encoding="utf-8") as f:
        json.dump(pattern_data, f, ensure_ascii=False, indent=2)

    # Keyword 向量表
    keyword_vecs = {}
    for i in range(vocab.num_keywords):
        vec = model.keyword_emb.weight[i].detach().cpu().tolist()
        keyword_vecs[vocab._id_to_keyword.get(i, f"?_{i}")] = [round(v, 6) for v in vec]

    keyword_data = {"dim": model.dim, "count": len(keyword_vecs),
                    "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "vectors": keyword_vecs}
    kp = output_dir / "keyword_vectors.json"
    with open(kp, "w", encoding="utf-8") as f:
        json.dump(keyword_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] Pattern 向量表: {pp} ({len(pattern_vecs)} 个)")
    print(f"[OK] Keyword 向量表: {kp} ({len(keyword_vecs)} 个)")

    # 同时导出 Excel（更直观）
    _export_vectors_excel(pattern_vecs, keyword_vecs, model.dim, output_dir)

    return str(pp), str(kp)


def _export_vectors_excel(pattern_vecs: dict, keyword_vecs: dict, dim: int,
                          output_dir: Path):
    """导出向量表为 Excel（每个 sheet 一张表，行=向量名，列=维度）。"""
    try:
        import pandas as pd
    except ImportError:
        return

    # Pattern sheet
    p_rows = []
    for name, vec in pattern_vecs.items():
        row = {"名称": name, "范数": round(sum(v*v for v in vec)**0.5, 4)}
        # 只取前 20 维到 Excel（128 列太宽了），完整数据在 JSON 里
        for d in range(min(dim, 20)):
            row[f"D{d}"] = vec[d]
        p_rows.append(row)

    p_df = pd.DataFrame(p_rows).sort_values("范数", ascending=False)

    # Keyword sheet
    k_rows = []
    for name, vec in keyword_vecs.items():
        row = {"名称": name, "范数": round(sum(v*v for v in vec)**0.5, 4)}
        for d in range(min(dim, 20)):
            row[f"D{d}"] = vec[d]
        k_rows.append(row)

    k_df = pd.DataFrame(k_rows).sort_values("范数", ascending=False)

    # 相似度 sheet（pattern × pattern 余弦相似度矩阵）
    # 只取前 30 个 pattern（多了 Excel 太宽）
    top_patterns = sorted(pattern_vecs.items(), key=lambda x: -sum(v*v for v in x[1])**0.5)[:30]
    p_names = [n[:50] for n, _ in top_patterns]
    p_vecs = [[v for v in vec] for _, vec in top_patterns]

    import numpy as np
    sim_matrix = np.zeros((len(p_names), len(p_names)))
    for i in range(len(p_names)):
        for j in range(len(p_names)):
            a, b = np.array(p_vecs[i]), np.array(p_vecs[j])
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            sim_matrix[i][j] = round(float(np.dot(a, b) / (na * nb + 1e-8)), 3)

    sim_df = pd.DataFrame(sim_matrix, index=p_names, columns=p_names)

    path = output_dir / "vectors.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        p_df.to_excel(writer, sheet_name="Patterns", index=False)
        k_df.to_excel(writer, sheet_name="Keywords", index=False)
        sim_df.to_excel(writer, sheet_name="Pattern相似度")

    print(f"[OK] 向量 Excel: {path}")
