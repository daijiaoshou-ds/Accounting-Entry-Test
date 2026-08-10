# -*- coding: utf-8 -*-
"""
训练数据文件管理 — V3.0（BGE 微调）专用 buckets_v2 格式

设计原则:
  - V2.1 classify() 每次跑完自动生成 training/{hash}.json（buckets_v2 格式）
  - 按桶聚合: 桶 → 科目组合 → 摘要列表，方便 AI 按桶审核（只删不改）
  - 人工审核后（reviewed: true）→ 合并为 training_data.json → 训练
  - 训练时展开为扁平 records

文件体系:
  nn/_storage/
  ├── training/                  ← 按哈希分离的"生"训练数据（需人工审核）
  │   ├── {hash1}.json           ← buckets_v2 格式
  │   └── {hash2}.json
  ├── training_data.json          ← 合并后的训练数据（仅从已审核的哈希文件合并）
  ├── fine_tuned/                 ← [训练产物] 微调后 BGE 模型
  ├── finance_classifier.pt       ← [训练产物] 分类头
  ├── subject_to_index.json       ← [训练产物] 科目开关索引
  ├── index_to_bucket.json        ← [训练产物] 桶索引
  └── training_log.json           ← [训练产物] 训练日志

单哈希文件格式 (buckets_v2):
  {
    "fingerprint": "abc123",
    "created_at": "2025-07-25",
    "format": "buckets_v2",
    "reviewed": false,
    "stats": {"total_records": 10, "buckets": 3, "dedup_merged": 2, "conflicts": 0},
    "buckets": {
      "存货采购": [
        {
          "subjects": ["应付账款[借]", "银行存款[贷]"],
          "records": [
            {"summary": "付杭州分公司货款", "count": 3},
            {"summary": "付北京分公司货款", "count": 5}
          ]
        }
      ],
      "费用报销": [ ... ]
    }
  }

审核方式（AI/人工）: 一个桶一个桶看。桶下每个「科目组合」节点及其摘要列表
若不属于该桶 → 直接删除该节点（或删除其中部分摘要）。不修改桶名、不新增记录。
删除后剩下的记录天然属于该桶，训练时展开即可。

旧格式（records_v1，扁平记录）文件会被 merge 自动跳过。
本模块顶层禁止 import torch/transformers —— V2.1 的 classify() 直接引用本模块。
"""

import difflib
import json
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

# 当前训练数据格式版本（buckets_v2: 按桶聚合，AI 按桶审核）
RECORDS_FORMAT = "buckets_v2"

# count 展开上限（防单条重复样本膨胀失控）
MAX_COUNT_EXPAND = 20


# ============================================================================
# 构建单哈希训练数据（records_v1）
# ============================================================================

def _dedup_key(summary: str, subjects: List[str]) -> Tuple[str, str]:
    """去重键: (摘要, 排序后的科目组合)。"""
    return summary, "|".join(sorted(subjects))


def _dedupe_similar_summaries(
    buckets_data: Dict[str, List[Dict]],
    threshold: float = 0.75,
) -> Dict[str, List[Dict]]:
    """同桶同科目组合内，摘要文本相似度 ≥ threshold 的只保留 1 条（count 累加）。

    背景: 序时账里"计提...25年1月 / 25年2月 / 25年3月..."这类只差期间的
    摘要语义完全相同，AI 审核时逐个看浪费上下文且稀释注意力。
    相似摘要 BGE 编码后向量几乎相同，去重对训练效果影响极小，count 累加
    保留训练权重。

    相似度: difflib.SequenceMatcher 字符级 ratio（短摘要效果好，零依赖）。
    保留策略: 保留 count 最大的，其余 count 累加进它（保持总权重不变）。

    Args:
        buckets_data: buckets_v2 聚合格式 {桶: [{subjects, records}]}
        threshold: 相似度阈值（默认 0.75 = 75%）

    Returns:
        去重后的 buckets_data（就地修改）
    """
    for groups in buckets_data.values():
        for group in groups:
            recs = group.get("records", [])
            if len(recs) <= 1:
                continue
            # count 降序，先处理高频（高频胜出）
            recs = sorted(recs, key=lambda r: -r.get("count", 1))
            kept: List[Dict] = []
            for rec in recs:
                merged = False
                for k in kept:
                    sim = difflib.SequenceMatcher(
                        None, rec["summary"], k["summary"]
                    ).ratio()
                    if sim >= threshold:
                        k["count"] = k.get("count", 1) + rec.get("count", 1)
                        merged = True
                        break
                if not merged:
                    kept.append(dict(rec))
            group["records"] = sorted(kept, key=lambda r: -r.get("count", 1))
    return buckets_data


def _aggregate_by_bucket(records: List[Dict]) -> Dict[str, List[Dict]]:
    """扁平 records → 按桶聚合: {桶: [{subjects, records: [{summary, count}]}]}。

    桶下按科目组合分组，同组合的摘要合并为列表（AI 按桶审核友好）。
    """
    buckets: Dict[str, Dict[str, Dict]] = {}
    for rec in records:
        subj_key = "|".join(sorted(rec["subjects"]))
        bucket_groups = buckets.setdefault(rec["bucket"], {})
        group = bucket_groups.setdefault(subj_key, {
            "subjects": rec["subjects"],
            "records": [],
        })
        group["records"].append({
            "summary": rec["summary"],
            "count": rec.get("count", 1),
        })

    # 桶内按科目组合排序；组合内按 count 降序（高频优先）
    result: Dict[str, List[Dict]] = {}
    for bucket, groups in buckets.items():
        result[bucket] = sorted(
            groups.values(),
            key=lambda g: -sum(r["count"] for r in g["records"]),
        )
    return result


def build_hash_training_data(
    df: pd.DataFrame,
    column_mapping: Dict[str, str],
    fingerprint: str = "",
    output_dir: str = None,
    skip_buckets: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """从 V2.1 分类结果提取训练记录 → 去重 → 按桶聚合 → 写入单哈希文件。

    返回数据 dict（同时写盘 training/{fingerprint}.json）。
    """
    from .data import DEFAULT_SKIP_BUCKETS, extract_training_records

    if output_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_dir = str(Path(NN_STORAGE_DIR) / "training")

    # 提取 [摘要, 科目组合, 桶] 训练记录
    raw_records = extract_training_records(
        df, column_mapping, skip_buckets=skip_buckets or DEFAULT_SKIP_BUCKETS,
    )

    # ── 文件内去重: 同键同桶 count 累加；同键不同桶 → count 多的胜出 ──
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    dedup_merged = 0
    conflicts = 0

    for rec in raw_records:
        key = _dedup_key(rec["summary"], rec["subjects"])
        if key in merged:
            dedup_merged += 1
            existing = merged[key]
            if existing["bucket"] != rec["bucket"]:
                conflicts += 1
                # 冲突: 谁 count 大谁留下（先到先得为 1）
                if rec.get("count", 1) > existing.get("count", 1):
                    existing["bucket"] = rec["bucket"]
            existing["count"] = existing.get("count", 1) + 1
        else:
            merged[key] = {
                "summary": rec["summary"],
                "subjects": rec["subjects"],
                "bucket": rec["bucket"],
                "count": 1,
            }

    records = sorted(
        merged.values(),
        key=lambda r: (r["bucket"], r["summary"]),
    )

    # 按桶聚合 + 相似摘要去重（同桶同组合 >75% 只留 1，count 累加）
    buckets_data = _aggregate_by_bucket(records)
    _dedupe_similar_summaries(buckets_data)

    # 去重后的实际摘要数（stats 用）
    deduped_records = _flatten_buckets(buckets_data)
    data = {
        "fingerprint": fingerprint or "unknown",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "format": RECORDS_FORMAT,
        "reviewed": False,
        "stats": {
            "total_records": len(deduped_records),
            "raw_records": len(records),
            "buckets": len(buckets_data),
            "dedup_merged": dedup_merged,
            "sim_deduped": len(records) - len(deduped_records),
            "conflicts": conflicts,
        },
        "buckets": buckets_data,
    }

    # 写入文件
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{fingerprint or 'unknown'}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 按桶拆分审核文件（parts/{桶}.json）——AI 一次审一个桶，避免上下文爆炸
    # 主文件已审核时 parts 继承审核状态（避免已审数据被跳过）
    _write_bucket_parts(
        buckets_data, fingerprint or "unknown", output_dir,
        reviewed=data.get("reviewed", False),
    )

    print(f"[OK] 训练数据已保存: {path}")
    print(f"   {len(deduped_records)} 条记录（相似去重 {len(records) - len(deduped_records)} 条）, "
          f"{len(buckets_data)} 桶, 精确去重 {dedup_merged} 条, 冲突 {conflicts} 条")

    return data


def _write_bucket_parts(
    buckets_data: Dict[str, List[Dict]],
    fingerprint: str,
    output_dir: Path,
    reviewed: bool = False,
) -> None:
    """将桶聚合数据拆分为逐桶审核文件: training/{hash}_parts/{桶}.json。

    每个 part 文件:
      {"fingerprint": "abc", "bucket": "存货采购", "reviewed": false,
       "groups": [{"subjects": [...], "records": [{"summary", "count"}, ...]}]}

    AI 审核时一次只给 1 个桶文件（上下文占用 ≈ 全量的 1/桶数），
    审完标记该桶 reviewed: true。merge_training_data 以 parts 文件为准
    （桶级审核），主文件仅作汇总视图。

    Args:
        reviewed: 主文件已审核时 parts 继承该状态（避免已审数据被跳过）
    """
    parts_dir = output_dir / f"{fingerprint}_parts"
    if parts_dir.exists():
        import shutil
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    for bucket, groups in buckets_data.items():
        part = {
            "fingerprint": fingerprint,
            "bucket": bucket,
            "reviewed": reviewed,
            "groups": groups,
        }
        safe_name = bucket.replace("/", "_").replace("\\", "_")
        with open(parts_dir / f"{safe_name}.json", "w", encoding="utf-8") as f:
            json.dump(part, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已拆分 {len(buckets_data)} 个桶审核文件: {parts_dir}")


# ============================================================================
# 合并所有已审核的哈希文件
# ============================================================================

def _merge_rec(
    merged: Dict[Tuple[str, str], Dict[str, Any]],
    summary: str,
    subjects: List[str],
    bucket: str,
    count: int,
):
    """合并一条记录进跨文件 dict（同键同桶累加；不同桶记入候选，最终众数裁决）。"""
    key = _dedup_key(summary, subjects)
    if key in merged:
        entry = merged[key]
        entry["buckets"][bucket] = entry["buckets"].get(bucket, 0) + count
        entry["total"] = entry.get("total", 0) + count
    else:
        merged[key] = {
            "summary": summary,
            "subjects": subjects,
            "buckets": {bucket: count},
            "total": count,
        }


def merge_training_data(
    training_dir: str = None,
    only_reviewed: bool = True,
    output_path: str = None,
) -> Dict[str, Any]:
    """合并所有（已审核的）哈希训练文件为一份训练数据。

    - 无 "records" 键的旧格式（legacy）文件跳过，计入 skipped_legacy_format
    - 跨文件去重: 同键同桶 count 累加；同键不同桶 → count 加权众数胜出
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
        return {"records": [], "source_hashes": []}

    # 跨文件合并: 同键 → {桶: count} 字典，最终按 count 加权众数裁决
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    source_hashes = []
    skipped_unreviewed = 0
    skipped_legacy = 0
    conflicts = 0

    for f in sorted(training_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            skipped_legacy += 1
            continue

        # 只接受 buckets_v2 新格式（subjects 完整）
        # 旧 records_v1（修复 NaN 前生成，85% 单科目）与更旧的 legacy 格式一律跳过，
        # 避免 subjects 不全的旧数据污染训练集
        if "buckets" not in data:
            skipped_legacy += 1
            continue

        # 按桶拆分审核文件（parts/{桶}.json）优先: AI 逐桶审核后桶级 reviewed
        parts_dir = training_dir / f"{f.stem}_parts"
        if parts_dir.exists():
            for pf in sorted(parts_dir.glob("*.json")):
                try:
                    part = json.loads(pf.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, KeyError):
                    continue
                if only_reviewed and not part.get("reviewed", False):
                    skipped_unreviewed += 1
                    continue
                bucket = part.get("bucket", pf.stem)
                source_hashes.append(f"{data.get('fingerprint', f.stem)}::{bucket}")
                for group in part.get("groups", []):
                    subjects = group.get("subjects", [])
                    for rec in group.get("records", []):
                        _merge_rec(merged, rec["summary"], subjects, bucket,
                                   rec.get("count", 1))
            continue

        if only_reviewed and not data.get("reviewed", False):
            skipped_unreviewed += 1
            continue

        source_hashes.append(data.get("fingerprint", f.stem))

        for rec in _flatten_buckets(data["buckets"]):
            _merge_rec(merged, rec["summary"], rec["subjects"], rec["bucket"],
                       rec.get("count", 1))

    # 冲突裁决: count 加权众数
    conflict_stats = {"total_conflicts": 0, "resolved_by_majority": 0}
    records = []
    for key, entry in merged.items():
        bucket, count = max(entry["buckets"].items(), key=lambda kv: kv[1])
        if len(entry["buckets"]) > 1:
            conflict_stats["total_conflicts"] += 1
            if entry["buckets"][bucket] >= entry["total"] / 2:
                conflict_stats["resolved_by_majority"] += 1
        records.append({
            "summary": entry["summary"],
            "subjects": entry["subjects"],
            "bucket": bucket,
            "count": count,
        })

    records.sort(key=lambda r: (r["bucket"], r["summary"]))

    # 按桶聚合 + 相似摘要去重
    buckets_data = _aggregate_by_bucket(records)
    _dedupe_similar_summaries(buckets_data)
    deduped = _flatten_buckets(buckets_data)
    buckets = sorted({r["bucket"] for r in deduped})

    merged_data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "format": RECORDS_FORMAT,
        "source_hashes": source_hashes,
        "total_hashes": len(source_hashes),
        "skipped_unreviewed": skipped_unreviewed,
        "skipped_legacy_format": skipped_legacy,
        "conflict_stats": conflict_stats,
        "stats": {
            "total_records": len(deduped),
            "raw_records": len(records),
            "buckets": len(buckets),
            "sim_deduped": len(records) - len(deduped),
        },
        "buckets": buckets_data,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 合并训练数据已保存: {output_path}")
    print(f"   {len(source_hashes)} 哈希, {len(records)} 条记录, {len(buckets)} 桶")
    if skipped_unreviewed:
        print(f"   跳过 {skipped_unreviewed} 个未审核的哈希文件")
    if skipped_legacy:
        print(f"   跳过 {skipped_legacy} 个旧格式文件（旧 records_v1/legacy 格式）")
    if conflict_stats["total_conflicts"]:
        print(f"   跨文件冲突 {conflict_stats['total_conflicts']} 条"
              f"（众数裁决 {conflict_stats['resolved_by_majority']} 条）")

    return merged_data


# ============================================================================
# 加载 / 查看 / 审核
# ============================================================================

def _flatten_buckets(buckets_data: Dict[str, List[Dict]]) -> List[Dict[str, Any]]:
    """buckets_v2 聚合格式 → 扁平 records（训练用）。"""
    records = []
    for bucket, groups in buckets_data.items():
        for group in groups:
            subjects = group.get("subjects", [])
            for rec in group.get("records", []):
                records.append({
                    "summary": rec["summary"],
                    "subjects": subjects,
                    "bucket": bucket,
                    "count": rec.get("count", 1),
                })
    return records


def load_merged_records(file_path: str = None) -> List[Dict[str, Any]]:
    """读取合并后的扁平 records 列表（buckets_v2 展开；缺失/旧格式 → []）。"""
    if file_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        file_path = str(Path(NN_STORAGE_DIR) / "training_data.json")

    path = Path(file_path)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return []

    if "buckets" in data:
        return _flatten_buckets(data["buckets"])
    if "records" in data:
        return data["records"]  # 旧 records_v1 兼容
    return []


def list_training_files(training_dir: str = None) -> List[Dict]:
    """列出所有哈希训练文件及其审核状态（buckets_v2 格式统计）。"""
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
        except (json.JSONDecodeError, KeyError):
            continue

        buckets_data = data.get("buckets", {})
        if buckets_data:
            total_records = sum(
                len(rec.get("records", []))
                for groups in buckets_data.values()
                for rec in groups
            )
            files.append({
                "filename": f.name,
                "fingerprint": data.get("fingerprint", ""),
                "reviewed": data.get("reviewed", False),
                "format": data.get("format", "buckets_v2"),
                "records": total_records,
                "buckets": len(buckets_data),
                "created_at": data.get("created_at", ""),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        else:
            # 旧格式（records_v1 / legacy）: 标记旧格式
            old_records = data.get("records", [])
            files.append({
                "filename": f.name,
                "fingerprint": data.get("fingerprint", ""),
                "reviewed": data.get("reviewed", False),
                "format": data.get("format", "legacy"),
                "records": len(old_records),
                "buckets": 0,
                "created_at": data.get("created_at", ""),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })

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
# 索引构建（训练时用）
# ============================================================================

def build_subject_switch_index(records: List[Dict]) -> Dict[str, int]:
    """收集全部 '科目[方向]' 开关 → 排序 → {科目[方向]: index}。"""
    switches: Set[str] = set()
    for rec in records:
        switches.update(rec.get("subjects", []))
    return {s: i for i, s in enumerate(sorted(switches))}


def build_bucket_index(records: List[Dict]) -> Dict[str, int]:
    """收集全部桶 → 排序 → {桶: index}。"""
    buckets = sorted({rec["bucket"] for rec in records})
    return {b: i for i, b in enumerate(buckets)}


# ============================================================================
# 训练/验证划分
# ============================================================================

def split_records(
    records: List[Dict],
    train_ratio: float = 0.8,
    seed: int = 42,
) -> Tuple[List[Dict], List[Dict]]:
    """按桶分层划分训练/验证集（80/20），count 展开（上限 MAX_COUNT_EXPAND）。

    每个样本一条记录（count 条重复样本会被展开，确保 count 大的
    模式在训练中贡献更多）。

    Returns:
        (train_records, val_records) — 展开后的样本列表
    """
    random.seed(seed)

    # 按桶分组
    by_bucket: Dict[str, List[Dict]] = defaultdict(list)
    for rec in records:
        by_bucket[rec["bucket"]].append(rec)

    train_samples: List[Dict] = []
    val_samples: List[Dict] = []

    for bucket, bucket_records in by_bucket.items():
        # 展开 + 打乱
        expanded = []
        for rec in bucket_records:
            n = min(rec.get("count", 1), MAX_COUNT_EXPAND)
            for _ in range(n):
                expanded.append(rec)
        random.shuffle(expanded)

        n_val = max(1, round(len(expanded) * (1 - train_ratio))) if len(expanded) > 1 else 0
        val_samples.extend(expanded[:n_val])
        train_samples.extend(expanded[n_val:])

    print(f"[OK] 数据划分: 训练 {len(train_samples)} 条, 验证 {len(val_samples)} 条"
          f"（按桶分层, 桶数 {len(by_bucket)}）")
    return train_samples, val_samples


# ============================================================================
# AI 审核指南生成（供 Claude Code 会话按桶审核用）
# ============================================================================

# 桶的业务含义（用户编写，供 AI 理解每个桶装什么业务）
# 注意: 桶名以 BUCKET_REGISTRY 为准（与训练数据一致）
BUCKET_MEANINGS = {
    "职工薪酬": "职工薪酬，字面意思，比如发放工资、计提工资等",
    "税费": "税费，字面意思，比如支付增值税、计提所得税等",
    "存货采购": "存货采购，字面意思，比如采购原材料、支付供应商货款等",
    "长期资产": "一切与长期资产相关的活动，比如采购固定资产、装修活动等",
    "销售收入": "销售收入，字面意思，比如销售货物、收到客户款项等",
    "借款筹资": "借款筹资，字面意思，比如借款、收到股东注资等",
    "利息收支": "字面意思，比如支付或计提利息等",
    "费用报销": "一切与日常费用有关的活动，比如支付办公费、差旅费等",
    "押金保证金": "一切与支付押金有关的活动，比如支付租金保证金、投标保证金等",
    "投资本金": "一切与投资支出有关的活动，比如长期投资等",
    "分红股利": "字面意思，比如分红",
    "生产制造": "字面意思，一切与生产制造有关的活动",
    "折旧摊销": "一切与长期资产折旧摊销有关的活动，比如固定资产折旧、无形资产摊销、使用权资产折旧等",
    "研发": "一切与研发有关的活动",
    "政府补助": "一切与政府补助有关的活动",
}


def generate_ai_review_guide(output_path: str = None) -> str:
    """生成 AI 审核指南（桶定义 + 审核规则）。

    自动排除硬规则桶（其他业务/资金内部往来/汇兑损益）——它们由 V2.1 代码
    if 语句写死分配，从不进入训练数据，指南里不应列出（避免 AI 困惑）。
    只保留会出现在训练数据中的语义桶。

    Args:
        output_path: 输出路径（默认 nn/_storage/training/AI_REVIEW_GUIDE.md）

    Returns:
        指南文本
    """
    from summary_cleaner.v2.config import BUCKET_REGISTRY
    from summary_cleaner.v2.config import load_buckets_json
    from .data import HARD_RULE_BUCKETS

    # 桶关键词资产（含每个桶的关键词列表）
    kw_data = {}
    try:
        kw_data = load_buckets_json()
    except Exception:
        kw_data = {}

    lines = []
    lines.append("# 训练数据 AI 审核指南（buckets_v2）")
    lines.append("")
    lines.append("你正在审核「序时账业务分类」训练数据。数据按桶聚合组织，你的任务是：")
    lines.append("**一个桶一个桶看，发现不属于该桶的记录，直接从桶里删除。**")
    lines.append("")
    lines.append("## 文件说明")
    lines.append("")
    lines.append("数据拆分为逐桶审核文件（`{hash}_parts/` 目录下，每桶一个 JSON 文件），")
    lines.append("一次审核一个桶文件即可，不要一次读入所有桶文件。")
    lines.append("")
    lines.append("## 单桶文件格式")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "fingerprint": "哈希", "bucket": "存货采购", "reviewed": false,')
    lines.append('  "groups": [')
    lines.append("    {")
    lines.append('      "subjects": ["应付账款[借]", "银行存款[贷]"],   ← 科目组合（借/贷）')
    lines.append('      "records": [')
    lines.append('        {"summary": "付杭州分公司货款", "count": 3},    ← 摘要 + 出现次数')
    lines.append('        {"summary": "付北京分公司货款", "count": 5}')
    lines.append("      ]")
    lines.append("    }")
    lines.append("  ]")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("- **groups** = 该桶下的「科目组合节点」列表")
    lines.append("- **subjects** = 该组合的科目+方向（如 应付账款[借]、银行存款[贷]）——业务结构线索")
    lines.append("- **records** = 该科目组合下的摘要列表（整句，最重要线索），count = 出现次数（高频更该审准）")
    lines.append("")
    lines.append("## 审核规则（核心）")
    lines.append("")
    lines.append("1. 一次看一个桶。对照下方桶定义，判断桶下每个「科目组合 + 摘要」是否真的属于该桶")
    lines.append("2. **不属于该桶** → 直接删除：")
    lines.append("   - 整个科目组合节点都不对 → 删除该组合节点（连同其 records）")
    lines.append("   - 组合对但个别摘要不对（如混入其他业务）→ 删除该摘要节点")
    lines.append("3. **拿不准 → 删除**（训练数据宁缺毋滥，只留下最明确、稳健的样本。")
    lines.append("   存疑样本会教坏模型，宁可少一点数据）")
    lines.append("4. **只删不改**：不要修改桶名，不要新增记录，不要移动记录到别的桶")
    lines.append("   - 删除是唯一操作。保留的都是你确认正确的样本")
    lines.append("5. 审完把该桶文件的 reviewed 改为 true")
    lines.append("6. 保留其他所有字段与结构")
    lines.append("")
    lines.append("## 桶定义（只会出现在训练数据中的桶）")
    lines.append("")

    def add_bucket(name):
        info = BUCKET_REGISTRY.get(name, {})
        clarity = info.get("clarity", "")
        subjects = list(info.get("subjects", {}).keys())
        lines.append(f"### {name}" + (f"（清晰度 {clarity}）" if clarity else ""))
        meaning = BUCKET_MEANINGS.get(name)
        if meaning:
            lines.append(f"- 它的含义：{meaning}")
        if subjects:
            lines.append(f"- 主要科目: " + "、".join(subjects[:12]))
        item = kw_data.get(name)
        kws = (item or {}).get("keywords", []) if isinstance(item, dict) else (item or [])
        if kws:
            lines.append(f"- 关键词: " + "、".join(kws[:15]))
        lines.append("")

    for name in kw_data:
        if name not in HARD_RULE_BUCKETS:
            add_bucket(name)
    for name in BUCKET_REGISTRY:
        if name not in kw_data and name not in HARD_RULE_BUCKETS:
            add_bucket(name)

    guide = "\n".join(lines)

    if output_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_path = str(Path(NN_STORAGE_DIR) / "training" / "AI_REVIEW_GUIDE.md")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(guide)
    print(f"[OK] AI 审核指南已生成: {output_path}")
    return guide
